#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KAMUCL 启动动画头像 / 吉祥物皮肤替换工具
=========================================

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

把 KAMUCL 启动动画（玻璃碎片汇聚）里那张头像、以及左上角头像点开的
「卡慕互动彩蛋」3D 吉祥物的皮肤，换成你导入的皮肤。

原理
----
两者都来自 app.asar 内模块里 base64 内嵌的 PNG：

  1. 启动动画头像
     `out/renderer/assets/splash-face-*.js`，内嵌 256x256 头部 PNG：
         const faceUrl = "data:image/png;base64,<...>";
  2. 吉祥物皮肤
     `out/renderer/assets/MascotStage-*.js`，内嵌 64x64 全身皮肤贴图：
         const skinUrl = "data:image/png;base64,<...>";
     （旁边还有 palm/print 两个 15x15 的手掌印反馈小图，不动它们）

这个工具会：
  1. 从你的皮肤 PNG 裁出头部（自动识别 64x32 / 64x64 / 高清皮肤）
  2. 编码成新的 PNG，分别写回 splash-face 和 MascotStage 模块
     —— 吉祥物要求 64x64 现代格式，64x32 旧版皮肤会先按 KAMUCL
     自己的算法转换成 64x64（补全手脚背面与叠加层）
  3. 重算 asar 头部里被替换文件的 SHA256 完整性字段
  4. 重建 asar（保持原有文件顺序与偏移，仅替换目标文件字节）

用法
----
    python patch_splash_avatar.py --list
    python patch_splash_avatar.py --skin "C:\\path\\to\\skin.png"
    python patch_splash_avatar.py --no-mascot --skin "skin.png"   # 只换启动动画

默认从脚本所在位置向上找 app.asar（把本文件夹放进 KAMUCL-runtime 即可），也可用 --asar 指定。
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
import sys
from datetime import datetime

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow：pip install Pillow", file=sys.stderr)
    sys.exit(1)

# 启动动画头像在 asar 里的路径（文件名带 hash，用前缀匹配）
SPLASH_FACE_PREFIX = "out/renderer/assets/splash-face-"
SPLASH_FACE_SUFFIX = ".js"

# 吉祥物皮肤所在模块（左上角头像点开的 3D 彩蛋）
MASCOT_PREFIX = "out/renderer/assets/MascotStage-"
MASCOT_SUFFIX = ".js"

# 吉祥物贴图规格：代码里写死 64x64（SKIN_SIZE = 64）
MASCOT_SKIN_SIZE = 64


def _default_search_bases():
    """
    给出默认的搜索起点：本文件夹的上一级和上两级（就近原则，不扫盘）。

    使用方法：把整个插件文件夹放进 KAMUCL 的 KAMUCL-runtime 目录
    （和版本文件夹同级），脚本从自己所在位置向上就能找到 app.asar。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.dirname(here),                      # 标准放法：在 KAMUCL-runtime 里
        os.path.dirname(os.path.dirname(here)),     # 放进版本文件夹里时
    ]

# 头像输出规格：与原图一致
OUT_SIZE = 256

# 皮肤尺寸白名单（宽, 高），用于判断导入图是不是皮肤贴图
SKIN_SIZES = {
    (64, 32), (64, 64),
    (128, 64), (128, 128),
    (256, 128), (256, 256),
    (512, 256), (512, 512),
    (1024, 512), (1024, 1024),
}


# ----------------------------------------------------------------- asar 读写

class AsarError(Exception):
    pass


class Asar:
    """最小可用的 asar 读写器（只支持读取 + 替换单个文件）。"""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                raise AsarError("文件太小，不是有效的 asar")
            self.header_size = struct.unpack("<I", head[4:8])[0]
            payload = f.read(self.header_size)
            json_len = struct.unpack("<I", payload[4:8])[0]
            self.header = json.loads(payload[8:8 + json_len])
        self.data_offset = 8 + self.header_size
        self.total_size = os.path.getsize(path)

    def find(self, rel_path):
        """按 asar 内相对路径找到文件条目。"""
        node = self.header
        for part in rel_path.split("/"):
            files = node.get("files") or {}
            if part not in files:
                return None
            node = files[part]
        return node if "offset" in node else None

    def list_splash_faces(self):
        """列出所有 splash-face 模块。"""
        out = []
        assets = (self.header.get("files", {})
                  .get("out", {}).get("files", {})
                  .get("renderer", {}).get("files", {})
                  .get("assets", {}).get("files", {}))
        for name in assets:
            if name.startswith("splash-face-") and name.endswith(SPLASH_FACE_SUFFIX):
                out.append("out/renderer/assets/" + name)
        return out

    def list_mascot_modules(self):
        """列出所有 MascotStage 模块（吉祥物）。"""
        out = []
        assets = (self.header.get("files", {})
                  .get("out", {}).get("files", {})
                  .get("renderer", {}).get("files", {})
                  .get("assets", {}).get("files", {}))
        for name in assets:
            if name.startswith("MascotStage-") and name.endswith(MASCOT_SUFFIX):
                out.append("out/renderer/assets/" + name)
        return out

    def read(self, rel_path):
        entry = self.find(rel_path)
        if entry is None:
            raise AsarError("asar 内找不到：" + rel_path)
        with open(self.path, "rb") as f:
            f.seek(self.data_offset + int(entry["offset"]))
            return f.read(entry["size"])

    def replace_many(self, replacements, out_path):
        """
        replacements: {rel_path: bytes}
        写出新 asar：保持文件原有顺序，逐个紧密写入（原包不做对齐填充），
        重算所有 offset/size，并更新被替换文件的 SHA256 完整性字段。

        头部是 Chromium Pickle 结构：
            [u32 4][u32 headerBufLen][u32 payloadSize][u32 jsonLen][json][pad4]
        数据区紧随其后，data_offset = 8 + headerBufLen。
        """
        # 按 offset 排序，拿到原有文件顺序
        entries = []

        def walk(node, rel):
            for name, val in (node.get("files") or {}).items():
                cur = rel + "/" + name if rel else name
                if "files" in val:
                    walk(val, cur)
                elif "offset" in val:
                    entries.append((int(val["offset"]), cur, val))

        walk(self.header, "")
        entries.sort()

        old = open(self.path, "rb")
        chunks = []
        cursor = 0
        try:
            for off, rel, val in entries:
                if rel in replacements:
                    content = replacements[rel]
                else:
                    old.seek(self.data_offset + off)
                    content = old.read(val["size"])
                # 紧密排列，无对齐填充（与原包一致）
                val["offset"] = str(cursor)
                val["size"] = len(content)
                if rel in replacements:
                    val["integrity"] = _integrity(content)
                cursor += len(content)
                chunks.append(content)
        finally:
            old.close()

        blob = json.dumps(self.header, separators=(",", ":")).encode("utf-8")
        json_len = len(blob)
        payload_size = 4 + json_len
        pad = (-payload_size) % 4
        payload_size += pad
        header_buf_len = 4 + payload_size

        with open(out_path, "wb") as new:
            new.write(struct.pack("<I", 4))
            new.write(struct.pack("<I", header_buf_len))
            new.write(struct.pack("<I", payload_size))
            new.write(struct.pack("<I", json_len))
            new.write(blob)
            new.write(b"\x00" * pad)
            for chunk in chunks:
                new.write(chunk)


def _integrity(data, block_size=4194304):
    blocks = [hashlib.sha256(data[i:i + block_size]).hexdigest()
              for i in range(0, len(data), block_size)] or \
             [hashlib.sha256(b"").hexdigest()]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": block_size,
        "blocks": blocks,
    }


# ------------------------------------------------------------- 头像裁切

def crop_head(skin: Image.Image) -> Image.Image:
    """从皮肤贴图裁出头部（含帽子/头发叠加层）。"""
    w, h = skin.size
    unit = w / 64.0
    modern = (h / unit) >= 64  # 高度达到 64 单位才有帽子层

    face = Image.new("RGBA", (8, 8))
    box = (int(8 * unit), int(8 * unit), int(16 * unit), int(16 * unit))
    face.paste(skin.crop(box), (0, 0))

    if modern:
        hat_box = (int(40 * unit), int(8 * unit), int(48 * unit), int(16 * unit))
        hat = skin.crop(hat_box)
        face = Image.alpha_composite(face, hat)

    return face.resize((OUT_SIZE, OUT_SIZE), Image.NEAREST)


def prepare_avatar(path: str) -> bytes:
    """读入任意图片，产出启动动画用的 256x256 PNG 字节。"""
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise SystemExit("无法读取图片：%s (%s)" % (path, exc))

    img = img.convert("RGBA")
    if img.size in SKIN_SIZES:
        face = crop_head(img)
        print("  识别为皮肤贴图 %dx%d，已裁出头部" % img.size)
    else:
        face = img.resize((OUT_SIZE, OUT_SIZE), Image.NEAREST)
        print("  非标准皮肤尺寸 %dx%d，整图缩放使用" % img.size)

    buf = io.BytesIO()
    face.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    print("  生成头像 PNG：%dx%d，%d 字节" % (OUT_SIZE, OUT_SIZE, len(data)))
    return data


def build_face_module(png_bytes: bytes, template: bytes) -> bytes:
    """把新 PNG 塞进 splash-face 模块，保留原模块结构。"""
    text = template.decode("utf-8", errors="replace")
    b64 = base64.b64encode(png_bytes).decode("ascii")

    # 匹配 const faceUrl = "data:image/png;base64,....";
    pattern = re.compile(r'(data:image/png;base64,)([A-Za-z0-9+/=]+)')
    if not pattern.search(text):
        raise SystemExit("模板里找不到 base64 头像数据，asar 结构可能已变化")
    return pattern.sub(lambda m: m.group(1) + b64, text, count=1).encode("utf-8")


# --------------------------------------------------------- 吉祥物皮肤准备

def _convert_legacy_skin(skin: Image.Image) -> Image.Image:
    """
    64x32 旧版皮肤 -> 64x64 现代格式。

    忠实移植 KAMUCL 自己的 convertSkinTo1_8 + fixOpaqueSkin：
      1. 旧图放到 64x64 画布上半部分（下半部分透明）
      2. 镜像复制四肢右侧到左侧，补全旧版缺失的背面贴图区域
      3. 整图完全不透明时，清掉帽子叠加层（否则会盖住头部）
    """
    if skin.size != (64, 32):
        skin = skin.resize((64, 32), Image.NEAREST)

    canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    canvas.paste(skin, (0, 0))

    # 背面镜像：源区域 -> 目标区域（同 convertSkinTo1_8 的 copySkin 参数），
    # 先全部取出再写回，避免读写同一画布互相污染
    flips = [
        (4, 16, 4, 4, 20, 48), (8, 16, 4, 4, 24, 48),
        (0, 20, 4, 12, 24, 52), (4, 20, 4, 12, 20, 52),
        (8, 20, 4, 12, 16, 52), (12, 20, 4, 12, 28, 52),
        (44, 16, 4, 4, 36, 48), (48, 16, 4, 4, 40, 48),
        (40, 20, 4, 12, 40, 52), (44, 20, 4, 12, 36, 52),
        (48, 20, 4, 12, 32, 52), (52, 20, 4, 12, 44, 52),
    ]
    parts = []
    for sx, sy, w, h, dx, dy in flips:
        part = canvas.crop((sx, sy, sx + w, sy + h)).transpose(Image.FLIP_LEFT_RIGHT)
        parts.append((part, (dx, dy)))
    for part, pos in parts:
        canvas.paste(part, pos)

    # 帽子叠加层清理：仅当上半部分完全不透明（旧版皮肤常见）
    if canvas.crop((0, 0, 64, 32)).split()[3].getextrema()[0] == 255:
        for x, y, w, h in ((40, 0, 8, 8), (48, 0, 8, 8), (32, 8, 8, 8),
                           (40, 8, 8, 8), (48, 8, 8, 8), (56, 8, 8, 8)):
            canvas.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    return canvas


def prepare_mascot_skin(path: str) -> bytes:
    """读入任意图片，产出吉祥物用的 64x64 皮肤 PNG 字节。"""
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise SystemExit("无法读取图片：%s (%s)" % (path, exc))

    img = img.convert("RGBA")
    w, h = img.size
    if (w, h) == (64, 32) or (w % 64 == 0 and h == w // 2):
        print("  识别为旧版皮肤 %dx%d，按 KAMUCL 算法转换为 64x64" % (w, h))
        img = _convert_legacy_skin(img)
    elif w == h and w % 64 == 0 and h >= 64:
        if (w, h) != (64, 64):
            img = img.resize((64, 64), Image.NEAREST)
            print("  识别为 %dx%d 皮肤，缩放为 64x64" % (w, h))
    else:
        img = img.resize((MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE), Image.NEAREST)
        print("  非标准皮肤尺寸 %dx%d，整图缩放为 64x64" % (w, h))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    print("  生成吉祥物皮肤 PNG：64x64，%d 字节" % len(data))
    return data


def build_mascot_module(png_bytes: bytes, template: bytes) -> bytes:
    """
    把新皮肤塞进 MascotStage 模块的 skinUrl，保留其余结构。

    只替换第一个 base64 数据（skinUrl），palmUrl / printUrl 原样保留。
    """
    text = template.decode("utf-8", errors="replace")
    b64 = base64.b64encode(png_bytes).decode("ascii")

    pattern = re.compile(r'(const skinUrl = "data:image/png;base64,)([A-Za-z0-9+/=]+)')
    if not pattern.search(text):
        raise SystemExit("模板里找不到 skinUrl 皮肤数据，asar 结构可能已变化")
    return pattern.sub(lambda m: m.group(1) + b64, text, count=1).encode("utf-8")


# ----------------------------------------------------------------- 主流程

def find_asar(explicit=None, kamu_dir=None):
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise SystemExit("指定的 asar 不存在：" + explicit)

    candidates = []
    bases = ([kamu_dir] if kamu_dir else []) + _default_search_bases()

    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        # <KAMUCL目录>\KAMUCL-runtime\<version>\resources\app.asar
        try:
            for root, dirs, files in os.walk(base):
                if "app.asar" in files:
                    p = os.path.join(root, "app.asar")
                    if p not in candidates:
                        candidates.append(p)
                if root.count(os.sep) - base.count(os.sep) > 4:
                    dirs[:] = []
        except OSError:
            continue
        if candidates:
            break

    if not candidates:
        raise SystemExit(
            "没找到 app.asar。请把整个插件文件夹放进 KAMUCL 的 KAMUCL-runtime\n"
            "目录（和版本文件夹同级），或用 --asar / --kamu-dir 指定，例如：\n"
            r'  --kamu-dir "C:\Users\你\Desktop\KAMUCL-runtime"'
        )
    if len(candidates) > 1:
        print("发现多个 app.asar：")
        for i, c in enumerate(candidates):
            print("  [%d] %s" % (i, c))
        print("使用第一个；如需指定请用 --asar")
    return candidates[0]



def _describe_png(data, rel, label):
    """打印模块内第一个内嵌 PNG 的信息。"""
    text = data.decode("utf-8", errors="replace")
    m = re.search(r'base64,([A-Za-z0-9+/=]+)', text)
    png_len = 0
    dims = "?"
    if m:
        try:
            raw = base64.b64decode(m.group(1))
            png_len = len(raw)
            dims = "%dx%d" % struct.unpack(">II", raw[16:24])
        except Exception:
            pass
    print("  %s（%s）" % (rel, label))
    print("    模块 %d 字节 | 内嵌 PNG %d 字节 | %s" % (len(data), png_len, dims))


def do_list(asar):
    print("asar：%s" % asar.path)
    print("数据区起始：%d，总大小：%d" % (asar.data_offset, asar.total_size))

    faces = asar.list_splash_faces()
    if faces:
        for rel in faces:
            _describe_png(asar.read(rel), rel, "启动动画头像")
    else:
        print("未找到 splash-face 模块")

    mascots = asar.list_mascot_modules()
    if mascots:
        for rel in mascots:
            _describe_png(asar.read(rel), rel, "吉祥物皮肤")
    else:
        print("未找到 MascotStage 模块")


def do_patch(asar, skin_path, dry_run=False, do_splash=True, do_mascot=True):
    replacements = {}
    done = []

    if do_splash:
        faces = asar.list_splash_faces()
        if not faces:
            raise SystemExit("未找到 splash-face 模块，无法替换启动动画头像")
        print("目标模块：%s" % ", ".join(faces))
        print("读取皮肤：%s" % skin_path)
        png = prepare_avatar(skin_path)
        for rel in faces:
            template = asar.read(rel)
            module = build_face_module(png, template)
            print("  %s：%d -> %d 字节" % (rel, len(template), len(module)))
            replacements[rel] = module
        done.extend(faces)

    if do_mascot:
        mascots = asar.list_mascot_modules()
        if not mascots:
            raise SystemExit("未找到 MascotStage 模块，无法替换吉祥物皮肤")
        print("目标模块：%s" % ", ".join(mascots))
        png = prepare_mascot_skin(skin_path)
        for rel in mascots:
            template = asar.read(rel)
            module = build_mascot_module(png, template)
            print("  %s：%d -> %d 字节" % (rel, len(template), len(module)))
            replacements[rel] = module
        done.extend(mascots)

    if dry_run:
        print("\n[dry-run] 未写入任何文件")
        return


    tmp = asar.path + ".tmp-patch"
    asar.replace_many(replacements, tmp)
    os.replace(tmp, asar.path)
    print("已写入：%s" % asar.path)

    # 校验
    check = Asar(asar.path)
    for rel in done:
        data = check.read(rel)
        entry = check.find(rel)
        ok = hashlib.sha256(data).hexdigest() == entry["integrity"]["hash"]
        print("  校验 %s：%s" % (rel, "通过" if ok else "失败！"))
    print("\n完成。重启 KAMUCL 即可看到新的启动动画头像和吉祥物皮肤。")



def main():
    ap = argparse.ArgumentParser(
        description="KAMUCL 启动动画头像 / 吉祥物皮肤替换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--skin", help="皮肤 PNG 路径（64x64 / 64x32 / 高清均可）")
    ap.add_argument("--asar", help="app.asar 完整路径")
    ap.add_argument("--kamu-dir", help="KAMUCL 目录（默认从脚本所在位置向上找）")
    ap.add_argument("--no-mascot", action="store_true", help="只换启动动画头像，不动吉祥物")
    ap.add_argument("--only-mascot", action="store_true", help="只换吉祥物皮肤，不动启动动画")
    ap.add_argument("--list", action="store_true", help="查看当前启动动画头像与吉祥物皮肤信息")
    ap.add_argument("--dry-run", action="store_true", help="试运行，不写入文件")
    args = ap.parse_args()

    if not (args.list or args.skin):
        ap.print_help()
        return

    asar_path = find_asar(args.asar, args.kamu_dir)
    asar = Asar(asar_path)

    if args.list:
        do_list(asar)
    else:
        if not os.path.isfile(args.skin):
            raise SystemExit("皮肤文件不存在：" + args.skin)
        do_patch(asar, args.skin, args.dry_run,
                 do_splash=not args.only_mascot,
                 do_mascot=not args.no_mascot)


if __name__ == "__main__":
    main()
