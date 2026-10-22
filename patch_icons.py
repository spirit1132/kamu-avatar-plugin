#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KAMUCL 图标替换工具
====================

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

替换 Windows 可执行文件里内嵌的图标（任务栏、窗口左上角、文件管理器里显示的那个）。

涉及两个文件：
  1. KAMUCL.exe            —— 启动器本体（运行时任务栏/窗口图标）
  2. KAMUCL-<版本>.exe     —— 便携安装器（文件管理器里看到的图标）

原理
----
图标存在 PE 文件的 .rsrc 资源节里：
  RT_GROUP_ICON (14) —— 图标组目录，描述每个尺寸的宽高/位深/数据长度
  RT_ICON (3)        —— 每个尺寸的实际图像数据（BMP 或 PNG）

本工具会：
  1. 读入你的 PNG，生成 7 种标准尺寸（16/24/32/48/64/128/256）
  2. 重新构建 RT_ICON 与 RT_GROUP_ICON 资源
  3. 原地重建 .rsrc 节（保持节表与节偏移不变，只改节内内容和 vsize）
  4. 同步修正 PE 可选头的 CheckSum
  5. 备份原文件

用法
----
    python patch_icons.py --list
    python patch_icons.py --icon "my_icon.png"
    python patch_icons.py --icon "my_icon.png" --target runtime

--target 可选 runtime / installer / all（默认 all，安装器存在才处理）
"""

import argparse
import glob
import os
import shutil
import struct
import sys
import zlib
from datetime import datetime

# 依赖检查与自动安装统一走 env_check；不要把这里写成裸 ImportError。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_check


def pillow():
    """确保 Pillow 可用后返回 PIL.Image（延迟导入，支持同进程刚装好的情况）。"""
    env_check.require('PIL', '图标生成需要 Pillow')
    from PIL import Image
    return Image

# 标准图标尺寸（Windows 会按需挑选）
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]

RT_ICON = 3
RT_GROUP_ICON = 14


# ------------------------------------------------------------------ PE 解析

class PEError(Exception):
    pass


class PE:
    def __init__(self, path):
        self.path = path
        self.raw = bytearray(open(path, 'rb').read())
        d = self.raw
        if d[:2] != b'MZ':
            raise PEError('不是 PE 文件（缺少 MZ）')
        self.pe_off = struct.unpack_from('<I', d, 0x3c)[0]
        if d[self.pe_off:self.pe_off + 4] != b'PE\x00\x00':
            raise PEError('PE 签名错误')
        coff = self.pe_off + 4
        self.machine, self.nsec = struct.unpack_from('<HH', d, coff)
        opt_size = struct.unpack_from('<H', d, coff + 16)[0]
        self.opt_off = coff + 20
        self.opt_size = opt_size
        magic = struct.unpack_from('<H', d, self.opt_off)[0]
        self.is64 = (magic == 0x20b)
        if magic not in (0x10b, 0x20b):
            raise PEError('未知的可选头 magic=0x%x' % magic)
        dd_off = self.opt_off + (112 if self.is64 else 96)
        self.dd_off = dd_off
        self.dd = [struct.unpack_from('<II', d, dd_off + 8 * i) for i in range(16)]
        self.sections = []
        sh = self.opt_off + opt_size
        self.sh_off = sh
        for i in range(self.nsec):
            o = sh + 40 * i
            name = bytes(d[o:o + 8]).rstrip(b'\x00').decode('ascii', 'replace')
            vsize, vaddr, rsize, raddr = struct.unpack_from('<IIII', d, o + 8)
            self.sections.append(dict(name=name, vsize=vsize, vaddr=vaddr,
                                      rsize=rsize, raddr=raddr, hdr=o))
        self.rsrc_rva, self.rsrc_size = self.dd[2]
        # CheckSum 字段位置
        self.checksum_off = self.opt_off + 64

    def rva2off(self, rva):
        for s in self.sections:
            if s['vaddr'] <= rva < s['vaddr'] + max(s['vsize'], s['rsize']):
                return s['raddr'] + (rva - s['vaddr'])
        return None

    def rsrc_section(self):
        for s in self.sections:
            if s['vaddr'] <= self.rsrc_rva < s['vaddr'] + max(s['vsize'], s['rsize']):
                return s
        raise PEError('找不到 .rsrc 节')

    def _entries(self, base_off, count):
        out = []
        for i in range(count):
            o = base_off + 8 * i
            name, off = struct.unpack_from('<II', self.raw, o)
            out.append((name, off))
        return out

    def walk(self):
        """返回 [(type_id, name_id, lang_id, data_rva, size)]"""
        d = self.raw
        base = self.rva2off(self.rsrc_rva)
        if base is None:
            return []
        res = []

        def name_id(v):
            return None if (v & 0x80000000) else (v & 0x7fffffff)

        n_named, n_id = struct.unpack_from('<HH', d, base + 12)
        for tname, toff in self._entries(base + 16, n_named + n_id):
            t_id = name_id(tname)
            if t_id is None or not (toff & 0x80000000):
                continue
            tbase = base + (toff & 0x7fffffff)
            nn, ni = struct.unpack_from('<HH', d, tbase + 12)
            for name, noff in self._entries(tbase + 16, nn + ni):
                nid = name_id(name)
                if nid is None or not (noff & 0x80000000):
                    continue
                nbase = base + (noff & 0x7fffffff)
                ln, li = struct.unpack_from('<HH', d, nbase + 12)
                for lang, loff in self._entries(nbase + 16, ln + li):
                    if loff & 0x80000000:
                        continue
                    dbase = base + loff
                    drva, dsize = struct.unpack_from('<II', d, dbase)
                    res.append((t_id, nid, lang & 0x7fffffff, drva, dsize))
        return res

    def read_res(self, rva, size):
        off = self.rva2off(rva)
        return bytes(self.raw[off:off + size])

    def group_icons(self):
        """{group_id: [ {w,h,bits,id,data} ]}"""
        res = self.walk()
        icons = {n: self.read_res(r, s) for t, n, l, r, s in res if t == RT_ICON}
        groups = {}
        for t, name, lang, rva, size in res:
            if t != RT_GROUP_ICON:
                continue
            raw = self.read_res(rva, size)
            cnt = struct.unpack_from('<H', raw, 4)[0]
            items = []
            for i in range(cnt):
                o = 6 + 14 * i
                w, h, cc, r1, planes, bits, nbytes, iid = struct.unpack_from('<BBBBHHIH', raw, o)
                items.append(dict(w=w or 256, h=h or 256, colors=cc, planes=planes,
                                  bits=bits, size=nbytes, id=iid, data=icons.get(iid)))
            groups[name] = items
        return groups

    def icon_lang(self):
        """返回图标资源用的语言 ID（通常是 0 或 1033）。"""
        for t, n, l, r, s in self.walk():
            if t == RT_GROUP_ICON:
                return l
        return 1033


# ------------------------------------------------------------- 资源段重建



def walk_entries(pe):
    """
    遍历资源树，返回 [(type_id, name_id, lang_id, entry_off, data_rva, size)]。

    entry_off 是"数据条目"（IMAGE_RESOURCE_DATA_ENTRY）相对节起始的偏移，
    改写它即可重定位资源数据，无需触碰目录结构。
    """
    d = pe.raw
    base = pe.rva2off(pe.rsrc_rva)
    out = []
    n_named, n_id = struct.unpack_from('<HH', d, base + 12)
    for i in range(n_named + n_id):
        o = base + 16 + 8 * i
        tname, toff = struct.unpack_from('<II', d, o)
        if (tname & 0x80000000) or not (toff & 0x80000000):
            continue
        t_id = tname & 0x7fffffff
        tbase = base + (toff & 0x7fffffff)
        nn, ni = struct.unpack_from('<HH', d, tbase + 12)
        for j in range(nn + ni):
            o2 = tbase + 16 + 8 * j
            nm, off = struct.unpack_from('<II', d, o2)
            if (nm & 0x80000000) or not (off & 0x80000000):
                continue
            n_id_v = nm & 0x7fffffff
            nbase = base + (off & 0x7fffffff)
            ln, li = struct.unpack_from('<HH', d, nbase + 12)
            for k in range(ln + li):
                o3 = nbase + 16 + 8 * k
                lang, off3 = struct.unpack_from('<II', d, o3)
                if off3 & 0x80000000:
                    continue
                entry_off = off3
                drva, size = struct.unpack_from('<II', d, base + entry_off)
                out.append((t_id, n_id_v, lang & 0x7fffffff, entry_off, drva, size))
    return out


def rebuild_rsrc(pe, new_icons, new_group):
    """
    重建 .rsrc 内容，替换 RT_ICON / RT_GROUP_ICON。

    策略（关键）：**完整保留原目录树与数据条目表，逐字节不动**，
    只把数据区搬到条目表之后并更新每个条目的 RVA/长度。
    这样目录结构（计数、偏移、命名资源、语言）完全不受影响，
    避免手工重建目录树时出错。

    new_icons: {icon_id: png_bytes}
    new_group: RT_GROUP_ICON 数据
    返回 (节内容 bytes, 实际用到的大小)
    """
    d = pe.raw
    rsrc = pe.rsrc_section()
    ents = walk_entries(pe)

    # 新数据区起点：紧接数据条目表末尾，4 字节对齐
    region_start = max(e[3] + 16 for e in ents)
    region_start = (region_start + 3) & ~3

    # 组装每个资源的新数据
    blobs = {}
    for t, n, l, eoff, drva, size in ents:
        key = (t, n, l)
        if t == RT_ICON and n in new_icons:
            blobs[key] = new_icons[n]
        elif t == RT_GROUP_ICON:
            blobs[key] = new_group
        else:
            off = pe.rva2off(drva)
            blobs[key] = bytes(d[off:off + size])

    # 原地复制整节，然后重写数据区
    out = bytearray(bytes(d[rsrc['raddr']:rsrc['raddr'] + rsrc['rsize']]))
    cursor = region_start
    for t, n, l, eoff, drva, size in ents:
        blob = blobs[(t, n, l)]
        cursor = (cursor + 3) & ~3
        if cursor + len(blob) > rsrc['rsize']:
            raise PEError(
                '数据放不下：需要 %d 字节，节容量 %d 字节。'
                % (cursor + len(blob), rsrc['rsize']))
        out[cursor:cursor + len(blob)] = blob
        struct.pack_into('<II', out, eoff, pe.rsrc_rva + cursor, len(blob))
        cursor += len(blob)

    return bytes(out), cursor


def patch_pe_icons(exe_path, png_bytes_by_size, dry_run=False, out_path=None):
    pe = PE(exe_path)
    rsrc = pe.rsrc_section()

    groups = pe.group_icons()
    if not groups:
        raise PEError('该文件没有图标资源')
    group_id = sorted(groups.keys())[0]
    old_items = groups[group_id]

    # 复用原有的图标 ID 序列与语言，保持资源树结构完全一致
    old_ids = [it['id'] for it in old_items]
    lang = pe.icon_lang()

    sizes = sorted(png_bytes_by_size)
    if len(sizes) > len(old_ids):
        raise PEError('新图标尺寸数(%d)超过原有槽位(%d)，无法原地替换'
                      % (len(sizes), len(old_ids)))

    # 建立 新图标ID -> PNG 数据
    new_icons = {}
    entries = []
    for idx, size in enumerate(sizes):
        iid = old_ids[idx]
        new_icons[iid] = png_bytes_by_size[size]
        entries.append(dict(w=size, h=size, bits=32, id=iid,
                            size=len(png_bytes_by_size[size])))

    # 未被替换的槽位：保留原数据，避免资源树出现空洞
    res = walk_entries(pe)
    for idx in range(len(sizes), len(old_ids)):
        iid = old_ids[idx]
        for t, n, l, eoff, drva, sz in res:
            if t == RT_ICON and n == iid:
                off = pe.rva2off(drva)
                new_icons[iid] = bytes(pe.raw[off:off + sz])
                entries.append(dict(w=old_items[idx]['w'], h=old_items[idx]['h'],
                                    bits=old_items[idx]['bits'], id=iid, size=sz))
                break

    # 重建 RT_GROUP_ICON（尺寸列表按槽位顺序）
    grp = struct.pack('<HHH', 0, 1, len(entries))
    for e in entries:
        w = 0 if e['w'] >= 256 else e['w']
        h = 0 if e['h'] >= 256 else e['h']
        grp += struct.pack('<BBBBHHIH', w, h, 0, 0, 1, 32, e['size'], e['id'])

    print('  .rsrc 节: raw=0x%x rsize=%d, 当前资源用 %d 字节, 余量 %d'
          % (rsrc['raddr'], rsrc['rsize'], pe.rsrc_size, rsrc['rsize'] - pe.rsrc_size))
    print('  图标槽位: %s（复用原有 ID 与语言 %d）' % (old_ids, lang))

    new_blob, used = rebuild_rsrc(pe, new_icons, grp)
    print('  重建后资源大小: %d 字节 (节内可用 %d)' % (used, rsrc['rsize']))
    if used > rsrc['rsize']:
        raise PEError(
            '新图标太大，重建后的资源(%d)超过节容量(%d)。\n'
            '  请换一张更简单的图标，或减少尺寸。' % (used, rsrc['rsize'])
        )

    if dry_run:
        print('  [dry-run] 未写入')
        return

    d = pe.raw

    # new_blob 已经是完整的节内容（rsize 字节），直接整段覆盖，
    # 保证节长度不变、后续节偏移不移动。
    if len(new_blob) != rsrc['rsize']:
        raise PEError('内部错误：重建后的节长度 %d 与原始 %d 不符'
                      % (len(new_blob), rsrc['rsize']))
    d[rsrc['raddr']:rsrc['raddr'] + rsrc['rsize']] = new_blob

    # 关键：**.rsrc 节的 vsize 必须保持原值不变**。
    # 缩小 vsize 会让 Windows 加载器判定映像布局不一致而拒绝加载
    # （CreateProcess 返回 ERROR_BAD_EXE_FORMAT / 193）。
    # 资源数据放在节内尾部（rsize 范围内），vsize 保持原值即可覆盖。
    #
    # 数据目录里的资源大小同样保持原值（资源查找依赖它做范围校验）。
    print('  节 vsize 保持原值 0x%x（不缩小）' % struct.unpack_from('<I', d, rsrc['hdr'] + 8)[0])

    # 修正 CheckSum
    _fix_checksum(d, pe)

    dest = out_path if out_path else exe_path
    open(dest, 'wb').write(d)
    print('  已写入：%s' % dest)


def _fix_checksum(d, pe):
    """
    重算 PE 可选头 CheckSum。

    注意：electron-builder 产出的 exe 通常把该字段留为 0，含义是"不校验"。
    若原值为 0，保持 0（最小改动、最安全）；原值非 0 时更新为正确值。
    """
    old = struct.unpack_from('<I', d, pe.checksum_off)[0]
    if old == 0:
        return
    struct.pack_into('<I', d, pe.checksum_off, 0)
    total = 0
    n = len(d)
    i = 0
    while i + 1 < n:
        if i == pe.checksum_off:
            i += 2          # 跳过 checksum 字段自身
            continue
        total += struct.unpack_from('<H', d, i)[0]
        total = (total & 0xffff) + (total >> 16)
        i += 2
    if i < n:
        total += d[i]
        total = (total & 0xffff) + (total >> 16)
    total = (total & 0xffff) + (total >> 16)
    total = (total & 0xffff) + (total >> 16)
    struct.pack_into('<I', d, pe.checksum_off, (total + n) & 0xffffffff)


# ---------------------------------------------------------------- 图像处理

def _png_bytes(img):
    import io
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def _quantize(img):
    """量化到调色板 PNG（大幅减小体积，PE 图标完全支持）。"""
    Image = pillow()
    try:
        return img.quantize(colors=255, method=Image.FASTOCTREE, dither=Image.NONE)
    except Exception:
        return img


def _png_len(im):
    return len(_png_bytes(im))


def make_icon_pngs(src_path, budget=None):
    """
    读入任意图片，产出 {size: png_bytes}（正方形）。

    budget 是**所有尺寸 PNG 合计**的字节上限。超出时按以下顺序降级，
    每降一级都重新核算实际总量，**直到真的放得下为止**：

      1. 去掉 256×256（很多 .rsrc 节根本塞不下它，且小图标用不到）
      2. 逐级量化到调色板（P 模式），且**只采纳确实变小了的**尺寸
      3. 继续丢大尺寸：128 → 64
      4. 最后丢掉 48，只保留 16/24/32

    旧版的问题：超预算时一次性砍到「量化 + 去掉 256」，然后**不再复核**，
    结果仍可能超出（安装器的 .rsrc 只有 16384 字节）。现在改成循环收敛，
    并在最后打印实际占用，方便判断。
    """
    Image = pillow()
    img = Image.open(src_path)
    img.load()
    img = img.convert('RGBA')

    # 非正方形：居中裁成正方形
    w, h = img.size
    if w != h:
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2,
                        (w - side) // 2 + side, (h - side) // 2 + side))
        print('  源图 %dx%d 非正方形，已居中裁为 %dx%d' % (w, h, side, side))

    def render(size, quantize):
        resample = (Image.NEAREST if (size <= 48 and w >= 4 * size)
                    else Image.LANCZOS)
        im = img.resize((size, size), resample)
        raw = _png_bytes(im)
        if quantize:
            q = _quantize(im)
            if q is not im:
                qb = _png_bytes(q)
                # 量化只在真的更小时才采纳（小图量化常常反而变大）
                if len(qb) < len(raw):
                    return qb
        return raw

    def build(sizes, quantize):
        return {s: render(s, quantize) for s in sizes}

    sizes = list(ICON_SIZES)
    out = build(sizes, quantize=False)
    total = sum(len(v) for v in out.values())
    print('  编码 RGBA：合计 %d 字节' % total)

    if budget is None or total <= budget:
        return out

    print('  超出预算 %d 字节，开始降级' % budget)

    # 降级阶梯：**先量化保住尺寸，再考虑丢尺寸**。
    # 量化通常能把体积砍掉 50%~80%，比直接丢掉 256/128 划算得多
    # （用户想要的是大图标能显示，而不是"能装上但只有小图"）。
    ladder = []
    for quant in (True, False):
        for allow_max in (256, 128, 64, 48):
            keep = [s for s in ICON_SIZES if s <= allow_max]
            ladder.append((keep, quant))
    # 保底：16/24/32
    ladder.append(([16, 24, 32], True))

    seen = set()
    best = None
    for keep, quant in ladder:
        key = (tuple(keep), quant)
        if key in seen:
            continue
        seen.add(key)
        cand = build(keep, quantize=quant)
        t = sum(len(v) for v in cand.values())
        tag = '量化' if quant else 'RGBA'
        biggest = max(keep)
        print('    尝试 最大 %3d（%s）：%d 字节 %s'
              % (biggest, tag, t, '✓' if t <= budget else '✗'))
        if t <= budget:
            best = (cand, t)
            break
        if best is None or t < sum(len(v) for v in best[0].values()):
            best = (cand, t)

    if best is None:
        best = (build([16, 24, 32], quantize=True), 0)

    cand, t = best
    if t > budget:
        print('  ! 已降级到最小可用集合，仍需 %d 字节（预算 %d）。' % (t, budget))
        print('    安装器 .rsrc 节容量有限，请换一张更简单的图标，')
        print('    或单独给安装器用简化图标：--icon simple.png --target installer')
    else:
        print('  ✓ 降级后合计 %d 字节，尺寸：%s'
              % (t, '/'.join(str(s) for s in sorted(cand))))
    return cand


# -------------------------------------------------------------------- 主流程

def _candidate_kamu_dirs(kamu_dir=None):
    """
    给出可能的 KAMUCL 安装目录：就近原则，不做全盘扫描。

    使用方法：把整个插件文件夹放进 KAMUCL 的 KAMUCL-runtime 目录
    （和版本文件夹同级），脚本从自己所在位置向上找最多两级。
    """
    out = []
    if kamu_dir:
        out.append(kamu_dir)
    here = os.path.dirname(os.path.abspath(__file__))
    out.append(os.path.dirname(here))                   # 标准放法：在 KAMUCL-runtime 里
    out.append(os.path.dirname(os.path.dirname(here)))  # 放进版本文件夹里时
    return out


def find_targets(kamu_dir=None):
    """返回 {'runtime': path_or_None, 'installer': path_or_None}"""
    runtime = None
    installer = None

    for base in _candidate_kamu_dirs(kamu_dir):
        if not base or not os.path.isdir(base):
            continue
        # 启动器本体：版本文件夹里的 KAMUCL.exe（base 就是 KAMUCL-runtime），
        # 或 base 本身就是版本文件夹
        for pat in ('*/KAMUCL.exe', 'KAMUCL.exe'):
            hits = glob.glob(os.path.join(base, pat))
            if hits:
                runtime = max(hits, key=os.path.getmtime)
                break
        # 便携安装器：KAMUCL-<版本>.exe，在本目录或上一级（不进子目录找）
        for d in (base, os.path.dirname(base)):
            hits = [h for h in glob.glob(os.path.join(d, 'KAMUCL-*.exe'))
                    if os.path.dirname(h).lower().rstrip('\\/') == d.lower().rstrip('\\/')]
            if hits:
                installer = max(hits, key=os.path.getmtime)
                break
        if runtime or installer:
            break
    return {'runtime': runtime, 'installer': installer}


def do_list(targets):
    for kind, path in targets.items():
        label = '启动器本体' if kind == 'runtime' else '便携安装器'
        if not path:
            print('%s: 未找到' % label)
            continue
        print('%s: %s' % (label, path))
        try:
            pe = PE(path)
        except Exception as e:
            print('  解析失败：%s' % e)
            continue
        rsrc = pe.rsrc_section()
        print('  %s  machine=0x%04x  节余量=%d'
              % ('x64' if pe.machine == 0x8664 else 'x86', pe.machine,
                 rsrc['rsize'] - pe.rsrc_size))
        for gid, items in sorted(pe.group_icons().items()):
            print('  图标组 id=%s（%d 个尺寸）' % (gid, len(items)))
            for it in items:
                print('      %3dx%-3d %2dbpp  %6d 字节'
                      % (it['w'], it['h'], it['bits'], it['size']))


def compute_icon_budget(pe):
    """
    算出这个 PE 的 .rsrc 节**最多能容纳多少字节的新图标数据**。

    重建时 `.rsrc` 会被整体重排：
        节内容 = [目录树 + 数据条目表] + [各资源数据，4 字节对齐]
    数据区从 region_start 开始，之后的可用空间是 rsize - region_start。

    旧版公式 `rsize - used + old_icon` 是错的：`used` 已经包含了旧图标，
    再加一次 old_icon 等于把旧图标算了两遍，预算会虚高到超过节容量。

    正确模型：
        可用 = rsize - region_start
               - group_size（重建的 RT_GROUP_ICON）
               - 非图标资源（对话框/版本信息/清单等，必须原样保留）
               - 对齐浪费

    returns (budget_bytes, details dict)
    """
    rsrc = pe.rsrc_section()
    ents = walk_entries(pe)

    # 数据区起点：所有数据条目之后，4 字节对齐
    region_start = max((e[3] + 16) for e in ents) if ents else 0
    region_start = (region_start + 3) & ~3

    # 非图标资源必须原样保留，它们占的空间不能算进预算
    non_icon = sum(e[5] for e in ents if e[0] != RT_ICON and e[0] != RT_GROUP_ICON)

    # RT_GROUP_ICON 会重建，预留 7 个尺寸的最坏情况
    group_size = 6 + 14 * len(ICON_SIZES)

    # 每个图标数据 4 字节对齐的浪费上界
    align_waste = 3 * len(ICON_SIZES)

    available = rsrc['rsize'] - region_start
    budget = available - non_icon - group_size - align_waste
    return max(0, budget), {
        'rsize': rsrc['rsize'],
        'region_start': region_start,
        'available': available,
        'non_icon': non_icon,
        'group_size': group_size,
    }


def do_patch(targets, icon_path, which, dry_run=False):
    # 先按每个目标的真实余量算出可用预算，取最紧的那个
    budgets = []
    active = []
    for kind, path in targets.items():
        if not path:
            continue
        if which != 'all' and kind != which:
            continue
        active.append((kind, path))
        try:
            pe = PE(path)
            b, detail = compute_icon_budget(pe)
            budgets.append(b)
            label = '启动器本体' if kind == 'runtime' else '便携安装器'
            print('  %s：.rsrc %d 字节，数据区起点 %d，非图标资源 %d'
                  % (label, detail['rsize'], detail['region_start'],
                     detail['non_icon']))
            print('  %s：可用图标预算 ≈ %d 字节' % (label, b))
        except Exception as e:
            print('%s 解析失败：%s' % (path, e))
    budget = min(budgets) if budgets else None

    print()
    print('生成图标尺寸（可用预算 %s 字节）：' % (budget if budget else '不限'))
    pngs = make_icon_pngs(icon_path, budget)
    for size in sorted(pngs):
        print('  %3dx%-3d %6d 字节' % (size, size, len(pngs[size])))
    print('  合计 %d 字节' % sum(len(v) for v in pngs.values()))

    ok_count = 0
    for kind, path in active:
        label = '启动器本体' if kind == 'runtime' else '便携安装器'
        print('\n[%s] %s' % (label, path))
        try:
            patch_pe_icons(path, pngs, dry_run)
            ok_count += 1
        except PEError as e:
            print('  失败：%s' % e)
        except PermissionError:
            print('  失败：文件被占用，请先关闭 KAMUCL 再试')
    return ok_count == len(active)



def main():
    ap = argparse.ArgumentParser(
        description='KAMUCL 图标替换工具',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--icon', help='新图标 PNG 路径（建议 256x256 或更大，正方形）')
    ap.add_argument('--target', default='all', choices=['all', 'runtime', 'installer'],
                    help='替换哪个：all（默认）/ runtime（启动器）/ installer（安装器）')
    ap.add_argument('--kamu-dir', help='KAMUCL 安装目录（默认从脚本所在位置向上找）')
    ap.add_argument('--list', action='store_true', help='查看当前图标信息')
    ap.add_argument('--dry-run', action='store_true', help='试运行，不写入')
    args = ap.parse_args()

    if not (args.list or args.icon):
        ap.print_help()
        return

    # 入口处确保依赖就绪：缺 Pillow 就装，装不上再退
    env_check.require_or_install('PIL', '生成各尺寸图标需要 Pillow')

    targets = find_targets(args.kamu_dir)
    if not any(targets.values()):
        raise SystemExit('没找到 KAMUCL 的可执行文件，请用 --kamu-dir 指定目录')

    if args.list:
        do_list(targets)
    else:
        if not os.path.isfile(args.icon):
            raise SystemExit('图标文件不存在：' + args.icon)
        do_patch(targets, args.icon, args.target, args.dry_run)


if __name__ == '__main__':
    main()
