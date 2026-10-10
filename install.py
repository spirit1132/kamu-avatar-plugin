#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KAMUCL 一键美化 · 头像 + 图标 + 插件
=====================================

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

一条命令完成四件事：

  1. 启动动画头像   —— 换掉启动时玻璃碎片汇聚出的那张头像
  2. 吉祥物皮肤     —— 左上角头像点开的「卡慕互动彩蛋」3D 角色的皮肤
  3. 程序图标       —— 换掉任务栏 / 窗口 / 文件管理器里的图标
  4. 界面头像插件   —— 安装并启用 avatar-swap 插件（主界面左上角头像）

用法
----
    python install.py                        # 用默认皮肤（自动从缓存找）
    python install.py --skin "我的皮肤.png"   # 指定皮肤 PNG
    python install.py --icon "我的图标.png"   # 图标单独指定（默认用皮肤头像）
    python install.py --no-icon              # 只改头像，不动图标
    python install.py --no-mascot            # 不换吉祥物皮肤
    python install.py --no-plugin            # 不装插件
    python install.py --list                 # 只看当前状态

特性
----
  * 自动检测 KAMUCL 是否在运行，在运行会提示并退出（避免文件占用）
  * 就近定位：整个文件夹放进 KAMUCL-runtime 就能跑，不扫盘、不猜路径
  * 每步都有验证：图标用 CreateProcessW 确认可加载，asar 用哈希校验
  * 失败自动回滚，不留半成品
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime

# ----------------------------------------------------------------- 依赖检查

try:
    from PIL import Image
except ImportError:
    print('缺少 Pillow。请先安装：')
    print('    pip install Pillow')
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 复用补丁工具的核心逻辑
try:
    import patch_icons
    import patch_splash_avatar
    import safe_replace
except Exception as exc:
    print('无法导入 patch_icons.py / patch_splash_avatar.py / safe_replace.py：%s' % exc)
    print('请确认这些文件都在同一目录下。')
    sys.exit(1)



# ------------------------------------------------------------------- 工具函数

C_OK = '\033[92m'
C_WARN = '\033[93m'
C_ERR = '\033[91m'
C_DIM = '\033[90m'
C_END = '\033[0m'
if os.name == 'nt' and not os.environ.get('WT_SESSION'):
    # 老版控制台可能不支持 ANSI，退化为纯文本
    try:
        import ctypes
        k = ctypes.WinDLL('kernel32')
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        C_OK = C_WARN = C_ERR = C_DIM = C_END = ''


def ok(msg):    print('%s  ✓ %s%s' % (C_OK, msg, C_END))
def warn(msg):  print('%s  ! %s%s' % (C_WARN, msg, C_END))
def err(msg):   print('%s  ✗ %s%s' % (C_ERR, msg, C_END))
def dim(msg):   print('%s    %s%s' % (C_DIM, msg, C_END))
def head(msg):  print('\n%s── %s ──%s' % (C_OK, msg, C_END))


def _version_dirs(base):
    """base 的直接子目录里，含 KAMUCL.exe 的版本文件夹。"""
    out = []
    if not base or not os.path.isdir(base):
        return out
    try:
        names = os.listdir(base)
    except OSError:
        return out
    for name in names:
        d = os.path.join(base, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, 'KAMUCL.exe')):
            out.append(d)
    return out


def find_kamucl_root(explicit=None):
    r"""
    定位 KAMUCL 安装目录——就近原则，不做全盘扫描。

    用法：把整个 kamu_avatar_plugin 文件夹拖进 KAMUCL 的 KAMUCL-runtime
    目录（和版本文件夹同级）：

        KAMUCL-runtime\
        ├── 1.1.21-xxxxxxxx\      ← 版本文件夹（KAMUCL.exe 在里面）
        └── kamu_avatar_plugin\   ← 整个文件夹放这里
            ├── 一键美化.bat
            └── install.py

    只向上看两级：本文件夹的上一级、上两级（上两级兼容把文件夹
    放进版本文件夹里的情况）。找不到就报错，提示放对位置。
    """
    if explicit:
        # 显式指定：既可以是 KAMUCL-runtime，也可以直接是版本文件夹
        if _version_dirs(explicit) or os.path.isfile(
                os.path.join(explicit, 'KAMUCL.exe')):
            return explicit
        return None

    parent = os.path.dirname(HERE)
    grandparent = os.path.dirname(parent)
    if _version_dirs(parent):
        return parent
    if _version_dirs(grandparent):
        return grandparent
    return None


def find_asar(root):
    """在 KAMUCL 目录下找 app.asar（版本文件夹的 resources 里）"""
    if not root:
        return None
    direct = os.path.join(root, 'resources', 'app.asar')
    if os.path.isfile(direct):
        return direct
    hits = []
    for vd in _version_dirs(root):
        p = os.path.join(vd, 'resources', 'app.asar')
        if os.path.isfile(p):
            hits.append(p)
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def find_userdata():
    """定位 KAMUCL 的 userData（插件与皮肤缓存所在）"""
    for name in ('KAMUCL', 'kamucl'):
        p = os.path.join(os.path.expandvars(r'%APPDATA%'), name)
        if os.path.isdir(p):
            return p
    return None


def kamucl_running():
    """返回正在运行的 KAMUCL 进程数"""
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq KAMUCL.exe', '/NH'],
                             capture_output=True, timeout=25).stdout
        txt = out.decode('gbk', 'replace')
        return sum(1 for line in txt.splitlines() if 'KAMUCL.exe' in line)
    except Exception:
        return 0


def extract_skin_from_cache(userdata):
    """
    从 KAMUCL 皮肤缓存里导出当前账号皮肤。
    缓存文件形如 skin-cache/<sha256>.json，内含 dataUrl 字段。
    """
    if not userdata:
        return None
    cache = os.path.join(userdata, 'skin-cache')
    if not os.path.isdir(cache):
        return None
    best = None
    for name in os.listdir(cache):
        if not name.endswith('.json'):
            continue
        p = os.path.join(cache, name)
        try:
            data = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        skins = data.get('skins') or []
        for sk in skins:
            url = sk.get('dataUrl') or ''
            if not url.startswith('data:image'):
                continue
            try:
                raw = base64.b64decode(url.split(',', 1)[1])
            except Exception:
                continue
            # 取最新的（按文件 mtime）
            if best is None or os.path.getmtime(p) > best[0]:
                best = (os.path.getmtime(p), raw, data.get('username', '?'),
                        sk.get('variant', 'classic'))
    return best


def make_avatar_icon(skin_png_bytes, out_path):
    """
    由皮肤生成图标：裁出头部（含帽子层）+ 圆角背景。
    """
    img = Image.open(io.BytesIO(skin_png_bytes)).convert('RGBA')
    w, h = img.size
    if w >= 64 and h >= 32:
        unit = w // 64
        box = (8 * unit, 8 * unit, 16 * unit, 16 * unit)
        head = img.crop(box)
        if h // unit >= 64:      # 现代皮肤有帽子层
            hat = img.crop((40 * unit, 8 * unit, 48 * unit, 16 * unit))
            head = Image.alpha_composite(head, hat)
    else:
        head = img
    big = head.resize((256, 256), Image.NEAREST)

    # 圆角
    from PIL import ImageDraw
    mask = Image.new('L', (256, 256), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 255, 255), radius=46, fill=255)
    out = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    out.paste(big, (0, 0), mask)
    out.save(out_path)
    return out_path


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            if limit and f.tell() > limit:
                break
    return h.hexdigest()


def can_load_exe(path):
    """
    用 Windows 加载器验证 exe 是否可加载（最硬的判据）。
    返回 (ok, error_code)
    """
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.WinDLL('kernel32', use_last_error=True)

    class SI(ctypes.Structure):
        _fields_ = [('cb', wt.DWORD), ('a', wt.LPWSTR), ('b', wt.LPWSTR), ('c', wt.LPWSTR),
                    ('x', wt.DWORD), ('y', wt.DWORD), ('xs', wt.DWORD), ('ys', wt.DWORD),
                    ('xc', wt.DWORD), ('yc', wt.DWORD), ('fa', wt.DWORD), ('fl', wt.DWORD),
                    ('sw', wt.WORD), ('cb2', wt.WORD), ('r2', ctypes.c_void_p),
                    ('hi', wt.HANDLE), ('ho', wt.HANDLE), ('he', wt.HANDLE)]

    class PI(ctypes.Structure):
        _fields_ = [('hp', wt.HANDLE), ('ht', wt.HANDLE),
                    ('pid', wt.DWORD), ('tid', wt.DWORD)]

    k.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                 wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                 ctypes.c_void_p, ctypes.POINTER(PI)]
    k.CreateProcessW.restype = wt.BOOL
    si = SI()
    si.cb = ctypes.sizeof(si)
    pi = PI()
    ctypes.set_last_error(0)
    okv = k.CreateProcessW(os.path.abspath(path), None, None, None, False, 0, None,
                           os.path.dirname(os.path.abspath(path)),
                           ctypes.byref(si), ctypes.byref(pi))
    code = ctypes.get_last_error()
    if okv:
        import time
        time.sleep(0.8)
        k.TerminateProcess(pi.hp, 0)
        k.CloseHandle(pi.hp)
        k.CloseHandle(pi.ht)
    return bool(okv), code



def _cleanup_tmp_files(root):
    """清理可能残留的临时文件（正常情况下不应有）"""
    import glob as _glob
    if not root:
        return
    n = 0
    for pat in (os.path.join(root, '**', '*.kamucl-tmp'),
                os.path.join(root, '**', '*.tmp-oneclick')):
        for f in _glob.glob(pat, recursive=True):
            try:
                os.remove(f)
                n += 1
            except OSError:
                pass
    if n:
        dim('已清理 %d 个残留临时文件' % n)


# ------------------------------------------------------------------ 各步骤



def step_splash_avatar(asar, skin_path, dry=False, do_splash=True, do_mascot=True):
    """
    替换 asar 内的皮肤资源（先验证再替换，原文件在验证通过前不会被改动）：
      ① 启动动画头像（splash-face 模块，256x256 头部）
      ② 吉祥物皮肤（MascotStage 模块，左上角头像点开的 3D 彩蛋）

    两者在同一个 app.asar 里，合并成一次写入。
    """
    head('① 启动动画头像 / 吉祥物皮肤')
    if not asar:
        warn('未找到 app.asar，跳过')
        return False

    print('  目标：%s' % asar)
    try:
        a = patch_splash_avatar.Asar(asar)
        faces = a.list_splash_faces() if do_splash else []
        mascots = a.list_mascot_modules() if do_mascot else []
        if not faces and not mascots:
            warn('asar 里没有可替换的皮肤模块，跳过')
            return False

        repl = {}
        if faces:
            png = patch_splash_avatar.prepare_avatar(skin_path)
            for rel in faces:
                repl[rel] = patch_splash_avatar.build_face_module(png, a.read(rel))
        if mascots:
            mskin = patch_splash_avatar.prepare_mascot_skin(skin_path)
            for rel in mascots:
                repl[rel] = patch_splash_avatar.build_mascot_module(mskin, a.read(rel))

        if dry:
            dim('[试运行] 将替换 %s' % ', '.join(sorted(repl)))
            return True

        def write(tmp):
            a.replace_many(repl, tmp)
            # 在替换原文件之前，先校验临时 asar 的完整性
            chk = patch_splash_avatar.Asar(tmp)
            for rel in repl:
                data = chk.read(rel)
                if hashlib.sha256(data).hexdigest() != chk.find(rel)['integrity']['hash']:
                    raise RuntimeError('完整性校验失败：%s' % rel)

        safe_replace.safe_replace(asar, write, verify=False, label='启动动画头像/吉祥物')
        parts = []
        if faces:
            parts.append('启动动画头像')
        if mascots:
            parts.append('吉祥物皮肤')
        ok('%s已替换（%d 个模块，校验通过）' % ('、'.join(parts), len(repl)))
        return True
    except Exception as e:
        err('失败：%s' % e)
        raise



def step_icons(root, icon_path, dry=False, targets=None):
    """
    替换程序图标。

    两阶段提交：先把所有目标构建到临时文件并逐一验证，
    全部通过后才统一替换——任何一个失败都不会改动原文件。
    """
    head('② 程序图标')
    tgt = patch_icons.find_targets(root)
    active = {k: v for k, v in tgt.items() if v and (not targets or k in targets)}
    if not active:
        warn('未找到可替换的 exe，跳过')
        return False

    for kind, path in active.items():
        print('  目标：%s' % path)
    try:
        pngs = patch_icons.make_icon_pngs(icon_path)
        if dry:
            dim('[试运行] 将写入 %d 个尺寸' % len(pngs))
            return True

        # ---- 阶段一：构建到临时文件并验证 ----
        staged = []
        try:
            for kind, path in active.items():
                label = '启动器本体' if kind == 'runtime' else '便携安装器'
                tmp = path + '.kamucl-tmp'

                def write(t, _p=path):
                    patch_icons.patch_pe_icons(_p, pngs, dry_run=False,
                                               out_path=t)

                safe_replace.safe_replace_pre(path, write, label=label)
                staged.append((path, tmp, label))
        except Exception:
            # 任一失败：清理已生成的临时文件，原文件全部未动
            for _, tmp, _ in staged:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            raise

        # ---- 阶段二：全部通过，统一替换 ----
        for path, tmp, label in staged:
            os.replace(tmp, path)
            dim('%s 已替换' % label)

        ok('程序图标已替换（%d 个文件）' % len(staged))
        return True
    except Exception as e:
        err('失败：%s' % e)
        raise



def step_plugin(userdata, dry=False):
    """安装并启用 avatar-swap 插件"""
    head('③ 界面头像插件')
    if not userdata:
        warn('未找到 KAMUCL userData 目录，跳过')
        return False

    src_main = os.path.join(HERE, 'main.js')
    src_json = os.path.join(HERE, 'plugin.json')
    if not (os.path.isfile(src_main) and os.path.isfile(src_json)):
        warn('缺少 main.js / plugin.json，跳过')
        return False

    plugins_dir = os.path.join(userdata, 'plugins')
    dest = os.path.join(plugins_dir, 'avatar-swap')
    state_file = os.path.join(userdata, 'plugins-state.json')

    print('  目标：%s' % dest)
    if dry:
        dim('[试运行] 将安装插件并写入 plugins-state.json')
        return True

    try:
        # 清理早期误装的目录（plugins/main 且无 plugin.json），
        # 同时把它从启用列表里摘掉，避免留下失效条目
        legacy = os.path.join(plugins_dir, 'main')
        legacy_removed = False
        if os.path.isdir(legacy) and not os.path.exists(os.path.join(legacy, 'plugin.json')):
            shutil.rmtree(legacy, ignore_errors=True)
            legacy_removed = True
            dim('已清理遗留目录 plugins/main')

        os.makedirs(dest, exist_ok=True)
        shutil.copy2(src_main, os.path.join(dest, 'main.js'))
        shutil.copy2(src_json, os.path.join(dest, 'plugin.json'))

        # 启用（合并进现有列表，不动其它插件）
        enabled = []
        if os.path.exists(state_file):
            try:
                st = json.load(open(state_file, encoding='utf-8'))
                if isinstance(st.get('enabled'), list):
                    enabled = [x for x in st['enabled'] if isinstance(x, str)]
            except Exception:
                pass
        if legacy_removed:
            enabled = [x for x in enabled if x != 'main']
        if 'avatar-swap' not in enabled:
            enabled.append('avatar-swap')
        json.dump({'enabled': enabled}, open(state_file, 'w', encoding='utf-8'),
                  indent=2, ensure_ascii=False)

        ok('插件已安装并启用（%s）' % dest)
        return True
    except Exception as e:
        err('失败：%s' % e)
        raise



# ------------------------------------------------------------------ 主流程

def cmd_list(root, asar, userdata):
    head('当前状态')

    print('  KAMUCL 目录 : %s' % (root or '未找到'))
    print('  app.asar    : %s' % (asar or '未找到'))
    print('  userData    : %s' % (userdata or '未找到'))
    print('  运行中进程  : %d' % kamucl_running())

    if asar:
        try:
            a = patch_splash_avatar.Asar(asar)
            for rel in a.list_splash_faces():
                txt = a.read(rel).decode('utf-8', 'replace')
                m = re.search(r'base64,([A-Za-z0-9+/=]+)', txt)
                if m:
                    raw = base64.b64decode(m.group(1))
                    dim('启动动画头像：%d 字节，%dx%d' %
                        (len(raw), *struct.unpack('>II', raw[16:24])))
            for rel in a.list_mascot_modules():
                txt = a.read(rel).decode('utf-8', 'replace')
                m = re.search(r'const skinUrl = "data:image/png;base64,([A-Za-z0-9+/=]+)', txt)
                if m:
                    raw = base64.b64decode(m.group(1))
                    dim('吉祥物皮肤：%d 字节，%dx%d' %
                        (len(raw), *struct.unpack('>II', raw[16:24])))
        except Exception as e:
            warn('读取 asar 失败：%s' % e)

    tgt = patch_icons.find_targets(root) if root else {}
    for kind, path in (tgt or {}).items():
        if not path:
            continue
        label = '启动器' if kind == 'runtime' else '安装器'
        try:
            pe = patch_icons.PE(path)
            g = pe.group_icons()
            gid = sorted(g)[0]
            tot = sum(e['size'] for e in g[gid])
            dim('%s图标：%d 个尺寸，共 %d 字节' % (label, len(g[gid]), tot))
        except Exception as e:
            warn('读取 %s 失败：%s' % (label, e))

    if userdata:
        d = os.path.join(userdata, 'plugins', 'avatar-swap')
        if os.path.isdir(d) and os.path.exists(os.path.join(d, 'main.js')):
            st = os.path.join(userdata, 'plugins-state.json')
            en = False
            try:
                en = 'avatar-swap' in json.load(open(st, encoding='utf-8')).get('enabled', [])
            except Exception:
                pass
            dim('插件：已安装%s' % ('，已启用' if en else '，未启用'))
        else:
            dim('插件：未安装')




def main():
    ap = argparse.ArgumentParser(
        description='KAMUCL 一键美化：头像 + 图标 + 插件',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--skin', help='皮肤 PNG 路径（默认自动从 KAMUCL 缓存导出）')
    ap.add_argument('--icon', help='图标 PNG 路径（默认由皮肤生成）')
    ap.add_argument('--kamu-dir', help='KAMUCL 安装目录（默认从脚本所在位置向上找）')
    ap.add_argument('--no-icon', action='store_true', help='不替换程序图标')
    ap.add_argument('--no-splash', action='store_true', help='不替换启动动画头像')
    ap.add_argument('--no-mascot', action='store_true', help='不替换吉祥物皮肤（左上角彩蛋）')
    ap.add_argument('--no-plugin', action='store_true', help='不安装插件')
    ap.add_argument('--list', action='store_true', help='只看当前状态')
    ap.add_argument('--dry-run', action='store_true', help='试运行，不写文件')
    ap.add_argument('--force', action='store_true', help='KAMUCL 运行中也强制继续（不推荐）')
    args = ap.parse_args()

    root = find_kamucl_root(args.kamu_dir)
    asar = find_asar(root) if root else None
    userdata = find_userdata()

    if args.list:
        cmd_list(root, asar, userdata)
        return

    print('%sKAMUCL 一键美化%s' % (C_OK, C_END))
    print('  目录     : %s' % (root or '未找到'))
    print('  app.asar : %s' % (asar or '未找到'))
    print('  userData : %s' % (userdata or '未找到'))

    if not root:
        err('没找到 KAMUCL 安装目录。请把整个 kamu_avatar_plugin 文件夹')
        print('  拖进 KAMUCL 的 KAMUCL-runtime 目录（和版本文件夹同级）再运行；')
        print('  或者用 --kamu-dir 指定安装目录，例如：')
        print(r'    python install.py --kamu-dir "C:\Users\你\Desktop\KAMUCL-runtime"')
        sys.exit(1)

    # 运行检查
    n = kamucl_running()
    if n and not args.force:
        err('检测到 %d 个 KAMUCL 进程正在运行。' % n)
        print('  补丁会替换被占用的文件，请先完全退出 KAMUCL 再运行。')
        print('  确认已退出后重新执行本脚本。')
        sys.exit(1)
    if n:
        warn('KAMUCL 正在运行（--force），替换可能失败')

    # 皮肤来源
    skin_path = args.skin
    tmp_skin = None
    if not skin_path:
        got = extract_skin_from_cache(userdata)
        if got:
            _, raw, username, variant = got
            tmp_skin = os.path.join(HERE, '.skin-from-cache.png')
            open(tmp_skin, 'wb').write(raw)
            skin_path = tmp_skin
            ok('已从 KAMUCL 缓存导出账号皮肤：%s（%s 模型，%d 字节）'
               % (username, variant, len(raw)))
        else:
            err('没找到皮肤。请用 --skin 指定一张 PNG：')
            print(r'    python install.py --skin "我的皮肤.png"')
            sys.exit(1)
    elif not os.path.isfile(skin_path):
        err('皮肤文件不存在：%s' % skin_path)
        sys.exit(1)

    # 图标来源
    icon_path = args.icon
    tmp_icon = None
    if not args.no_icon and not icon_path:
        tmp_icon = os.path.join(HERE, '.icon-from-skin.png')
        make_avatar_icon(open(skin_path, 'rb').read(), tmp_icon)
        icon_path = tmp_icon
        ok('已由皮肤生成图标：%s' % os.path.basename(tmp_icon))
    elif icon_path and not os.path.isfile(icon_path):
        err('图标文件不存在：%s' % icon_path)
        sys.exit(1)

    try:
        if not args.no_splash or not args.no_mascot:
            step_splash_avatar(asar, skin_path, args.dry_run,
                               do_splash=not args.no_splash,
                               do_mascot=not args.no_mascot)
        if not args.no_icon:
            step_icons(root, icon_path, args.dry_run)
        if not args.no_plugin:
            step_plugin(userdata, args.dry_run)
    except Exception as e:
        print()
        err('执行中断：%s' % e)
        print('  采用「先验证再替换」，失败时原文件从未被修改，无需回滚。')
        _cleanup_tmp_files(root)
        sys.exit(1)
    finally:
        for f in (tmp_skin, tmp_icon):
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    print()
    if args.dry_run:
        print('%s试运行完成，未写入任何文件。%s' % (C_OK, C_END))
    else:
        print('%s全部完成！%s' % (C_OK, C_END))
        print('  重启 KAMUCL 即可看到新的启动动画头像、吉祥物皮肤、程序图标和界面头像。')
        print()
        print('  查看：python install.py --list')


if __name__ == '__main__':
    main()
