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

# 依赖检查与自动安装统一走 env_check；不要把这里写成裸 ImportError。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_check


def pillow():
    """确保 Pillow 可用后返回 PIL.Image（延迟导入，支持同进程刚装好的情况）。"""
    env_check.require('PIL', '皮肤裁切需要 Pillow')
    from PIL import Image
    return Image

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

        注意：本方法会**就地修改 self.header** 里的 offset/size/integrity。
        调用之后同一个 Asar 实例读到的是新布局，不再是原包内容；
        需要比对前后差异时，必须在调用前先读好快照，或重新构造 Asar。
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

def crop_head(skin):
    """从皮肤贴图裁出头部（含帽子/头发叠加层）。"""
    Image = pillow()
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
    Image = pillow()
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


# --------------------------------------------------------- 内嵌资源定位

# 打包器在不同 KAMUCL 版本里的压缩程度不同：
#   1.1.21  const faceUrl = "data:image/png;base64,...";      变量名可读
#   1.1.48  const U="data:image/png;base64,...";export{U as f}  变量名被 mangle 成单字母
# 所以不能只认变量名。这里提供两条路径：
#   ① 优先按已知变量名匹配（最精确，且能保证替换的是正确那个）
#   ② 失败则按 data URL 出现顺序 + PNG 尺寸校验定位（尺寸对得上才替换）
DATA_URL_RE = re.compile(r'data:image/png;base64,([A-Za-z0-9+/=]+)')


def _png_size(b64_text):
    """从 base64 编码的 PNG 里读出 (宽, 高)；失败返回 None。"""
    try:
        raw = base64.b64decode(b64_text[:512] + "=" * (-len(b64_text[:512]) % 4))
        # PNG IHDR：8 字节签名 + 4 长度 + 4 类型，之后是大端宽高
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", raw[16:24])
    except Exception:
        return None


def _replace_nth_data_url(text, b64, index, expect_size=None):
    """
    替换第 index 个（从 0 起）data URL 的 base64 内容。

    expect_size 给定时先校验原图尺寸，不匹配就拒绝替换——
    避免在结构变化后误把手掌/指纹小图当成皮肤。
    """
    matches = list(DATA_URL_RE.finditer(text))
    if len(matches) <= index:
        return None
    m = matches[index]
    if expect_size is not None:
        got = _png_size(m.group(1))
        if got != expect_size:
            return None
    return text[:m.start(1)] + b64 + text[m.end(1):]


def build_face_module(png_bytes: bytes, template: bytes) -> bytes:
    """
    把新 PNG 塞进 splash-face 模块，保留原模块结构。

    该模块只有一个内嵌 PNG（启动动画头像），所以两条路径指向同一处：
      ① const faceUrl = "data:image/png;base64,..."（1.1.21 等未压缩版本）
      ② const U="data:image/png;base64,..."（1.1.48 等 mangle 版本）
    """
    text = template.decode("utf-8", errors="replace")
    b64 = base64.b64encode(png_bytes).decode("ascii")

    named = re.compile(
        r'(const\s+faceUrl\s*=\s*"data:image/png;base64,)([A-Za-z0-9+/=]+)')
    if named.search(text):
        return named.sub(lambda m: m.group(1) + b64, text, count=1).encode("utf-8")

    # 退回按序号定位：这个模块里只应有 1 个 256x256 的 data URL
    try:
        from PIL import Image  # noqa: F401  (仅确认依赖可用)
        expected = (OUT_SIZE, OUT_SIZE)
    except Exception:
        expected = None
    out = _replace_nth_data_url(text, b64, 0, expected)
    if out is not None:
        return out.encode("utf-8")

    # 兜底：不校验尺寸，直接换第一个（模块只有一个 PNG 时安全）
    out = _replace_nth_data_url(text, b64, 0)
    if out is not None:
        return out.encode("utf-8")

    raise SystemExit("模板里找不到 base64 头像数据，asar 结构可能已变化")


# ------------------------------------------------------------- 皮肤模型识别

# KAMUCL 的 SkinObject.slim 字段默认 false，手臂 UV 宽度取 slim ? 3 : 4。
# 吉祥物模块自己从不设置它，所以皮肤是 slim 时必须由我们显式写 modelType/slim。
#
# 判定算法与 KAMUCL 的 detectSkinVariant 完全一致：
#   把皮肤缩放到 64x64，取 (54,20) 起 1x12 的一整列 alpha，
#   有任何不透明像素 => classic；全透明 => slim。
#   原理是 classic 手臂背面占 x=54..55 共 4 列，slim 只占 x=54..55 的 3 列，
#   多出来的那一列在 slim 皮肤里必然留空。
SLIM_PROBE = (54, 20, 1, 12)


def detect_skin_variant(skin):
    """
    返回 'slim' / 'classic'。

    与 KAMUCL detectSkinVariant 同算法；取不到数据时返回 None（调用方按 classic 处理）。
    """
    try:
        Image = pillow()
        probe = skin.convert("RGBA")
        if probe.size != (64, 64):
            probe = probe.resize((64, 64), Image.NEAREST)
        x, y, w, h = SLIM_PROBE
        column = probe.crop((x, y, x + w, y + h))
        # getdata() 在 Pillow 14 会被移除，优先用 tobytes() 直接取 RGBA 字节
        raw = column.tobytes()
        for i in range(3, len(raw), 4):
            if raw[i] != 0:          # 每 4 字节的最后一个就是 alpha
                return "classic"
        return "slim"
    except Exception:
        return None


def _convert_legacy_skin(skin):
    """
    64x32 旧版皮肤 -> 64x64 现代格式。

    忠实移植 KAMUCL 自己的 convertSkinTo1_8 + fixOpaqueSkin：
      1. 旧图放到 64x64 画布上半部分（下半部分透明）
      2. 镜像复制四肢右侧到左侧，补全旧版缺失的背面贴图区域
      3. 整图完全不透明时，清掉帽子叠加层（否则会盖住头部）
    """
    Image = pillow()
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


def prepare_mascot_skin(path: str, variant: str = None):
    """
    读入任意图片，产出吉祥物用的 64x64 皮肤 PNG 字节。

    返回 (png_bytes, variant)，variant 为 'slim' / 'classic'。
    variant 由调用方传入时直接采用（例如皮肤缓存里已有 variant 字段，
    比像素探测更可靠）；否则按 KAMUCL 的 detectSkinVariant 探测。

    注意：slim 皮肤不能靠「补全成 classic」来绕过——slim 的手臂贴图只有 3 列宽，
    硬当 4 列用会把相邻面的像素采进来（手臂多出一条肉色竖纹）。
    正确做法是保留 slim 布局，并把模型类型一起写进模块。
    """
    try:
        Image = pillow()
        img = Image.open(path)
        img.load()
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit("无法读取图片：%s (%s)" % (path, exc))

    img = img.convert("RGBA")
    w, h = img.size
    legacy = False
    if (w, h) == (64, 32) or (w % 64 == 0 and h == w // 2):
        print("  识别为旧版皮肤 %dx%d，按 KAMUCL 算法转换为 64x64" % (w, h))
        img = _convert_legacy_skin(img)
        legacy = True
    elif w == h and w % 64 == 0 and h >= 64:
        if (w, h) != (64, 64):
            img = img.resize((64, 64), Image.NEAREST)
            print("  识别为 %dx%d 皮肤，缩放为 64x64" % (w, h))
    else:
        img = img.resize((MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE), Image.NEAREST)
        print("  非标准皮肤尺寸 %dx%d，整图缩放为 64x64" % (w, h))

    # 模型类型：优先用调用方给的（缓存里的 variant），否则自行探测
    if variant not in ("slim", "classic"):
        variant = detect_skin_variant(img)
        if variant is None:
            variant = "classic"
    if legacy:
        # 旧版皮肤没有手臂以外的 slim 信息，按转换结果探测更准
        variant = detect_skin_variant(img) or variant
    print("  皮肤模型：%s（手臂贴图宽度 %d）"
          % (variant, 3 if variant == "slim" else 4))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    print("  生成吉祥物皮肤 PNG：64x64，%d 字节" % len(data))
    return data, variant


# 吉祥物模块里 skin 赋值语句的通用形态。压缩前后：
#   1.1.21  player.skin.map = skin;
#   1.1.48  h.skin.map=b,
# 共同点是 `<对象标识符>.skin.map` 后面跟赋值。用捕获组接住前缀，才能在后面插入声明。
SKIN_MAP_RE = re.compile(
    r'([A-Za-z_$][\w$]*)\.skin\.map\s*=\s*[A-Za-z_$][\w$]*')

# 已写入的模型类型声明，形如 `XX.skin.modelType="slim"` / `=.skin.modelType = "default";`
MODEL_DECL_RE = re.compile(
    r'([A-Za-z_$][\w$]*)\.skin\.modelType\s*=\s*"(?:slim|default)"\s*;?')


def build_mascot_module(png_bytes: bytes, template: bytes, variant: str = None) -> bytes:
    """
    把新皮肤塞进 MascotStage 模块，并把模型类型一起写进去。

    只替换第一个 64x64 的 data URL（皮肤），palm/print 两张 15x15 小图原样保留。

    兼容不同压缩程度：
      · 皮肤定位
          ① const skinUrl = "data:image/png;base64,..."   （1.1.21 等可读版本）
          ② const La="data:image/png;base64,..."          （1.1.48 等 mangle 版本）
        两路都不中时，按「第 1 个 data URL 且尺寸为 64x64」定位。
        尺寸校验是关键：模块里另有 2 个 15x15 小图，靠尺寸区分，绝不误伤。

      · 模型类型声明
        找到 `<x>.skin.map=<y>` 之后插入 `<x>.skin.modelType="slim"|"default";`。
        1.1.48 的压缩版本里 SkinObject 的 setter 依然存在：
            get modelType(){return this.slim?"slim":"default"}
            set modelType(e){this.slim=e==="slim",this.modelListeners.forEach(t=>t())}
        所以写入同样生效，会把手臂 mesh 的 scale.x / UV 宽度在 4 与 3 之间切换。

    variant 为 None 时按 classic 处理（等价于 KAMUCL 原本的默认值）。
    """
    text = template.decode("utf-8", errors="replace")
    b64 = base64.b64encode(png_bytes).decode("ascii")

    # ---- 1. 替换皮肤数据 ----
    named = re.compile(
        r'(const\s+skinUrl\s*=\s*"data:image/png;base64,)([A-Za-z0-9+/=]+)')
    if named.search(text):
        text = named.sub(lambda m: m.group(1) + b64, text, count=1)
    else:
        # 按序号 + 尺寸定位：第 0 个，且必须是 64x64
        replaced = _replace_nth_data_url(
            text, b64, 0, (MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE))
        if replaced is not None:
            text = replaced
        else:
            # 再退一步：找第一个「尺寸正好 64x64」的 data URL，不限定序号
            hit = None
            for i, m in enumerate(DATA_URL_RE.finditer(text)):
                if _png_size(m.group(1)) == (MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE):
                    hit = i
                    break
            if hit is None:
                raise SystemExit(
                    "模板里找不到 64x64 的皮肤数据（skinUrl）。\n"
                    "  KAMUCL 结构可能已变化，请检查 MascotStage 模块。")
            text = _replace_nth_data_url(text, b64, hit,
                                         (MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE))

    # ---- 2. 写入模型类型 ----
    want = "slim" if variant == "slim" else "default"

    # 已存在声明：就地改值（幂等，可 slim <-> classic 来回切）
    existing = MODEL_DECL_RE.search(text)
    if existing:
        decl = '%s.skin.modelType="%s";' % (existing.group(1), want)
        text = text[:existing.start()] + decl + text[existing.end():]
        return text.encode("utf-8")

    m = SKIN_MAP_RE.search(text)
    if not m:
        raise SystemExit(
            "模板里找不到 `<x>.skin.map=<y>` 赋值语句，无法写入模型类型。\n"
            "  KAMUCL 结构可能已变化，请检查 MascotStage 模块。")

    obj = m.group(1)
    # 原语句结尾可能是 `;` 或 `,`（压缩版用逗号串联声明），插在其后即可
    end = m.end()
    tail = text[end:end + 1]
    if tail in (";", ","):
        end += 1
    decl = '%s.skin.modelType="%s";' % (obj, want)
    text = text[:end] + decl + text[end:]
    return text.encode("utf-8")


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



def _declared_variant(text: str) -> str:
    """
    读出模块里声明的模型类型。

    兼容两种写法：
        player.skin.modelType = "slim";      （1.1.21 等可读版本）
        h.skin.modelType="slim";             （1.1.48 等 mangle 版本）

    未声明时按 KAMUCL 的默认值算 classic（SkinObject.slim 初值为 false）。
    """
    m = MODEL_DECL_RE.search(text)
    if not m:
        return "classic"
    return "slim" if '"slim"' in m.group(0) else "classic"


def _skin_data_url(text: str):
    """
    取出吉祥物模块里的皮肤 base64 文本（用于 --list 显示）。

    优先认 skinUrl 变量名，退回「第一个 64x64 的 data URL」。
    """
    m = re.search(r'const\s+skinUrl\s*=\s*"data:image/png;base64,([A-Za-z0-9+/=]+)',
                  text)
    if m:
        return m.group(1)
    for dm in DATA_URL_RE.finditer(text):
        if _png_size(dm.group(1)) == (MASCOT_SKIN_SIZE, MASCOT_SKIN_SIZE):
            return dm.group(1)
    return None


def _describe_png(data, rel, label):
    """打印模块内目标 PNG 的信息（头像取第 1 个，吉祥物按尺寸取 64x64 那个）。"""
    text = data.decode("utf-8", errors="replace")

    b64_text = None
    if label == "吉祥物皮肤":
        b64_text = _skin_data_url(text)
    else:
        m = re.search(r'const\s+faceUrl\s*=\s*"data:image/png;base64,([A-Za-z0-9+/=]+)',
                      text)
        b64_text = m.group(1) if m else None
        if b64_text is None:
            first = DATA_URL_RE.search(text)
            b64_text = first.group(1) if first else None

    png_len = 0
    dims = "?"
    if b64_text:
        try:
            raw = base64.b64decode(b64_text)
            png_len = len(raw)
            dims = "%dx%d" % struct.unpack(">II", raw[16:24])
        except Exception:
            pass
    print("  %s（%s）" % (rel, label))
    print("    模块 %d 字节 | 内嵌 PNG %d 字节 | %s" % (len(data), png_len, dims))

    # 吉祥物模块额外报告皮肤模型类型
    if label == "吉祥物皮肤" and b64_text:
        try:
            Image = pillow()
            skin = Image.open(io.BytesIO(base64.b64decode(b64_text)))
            detected = detect_skin_variant(skin) or "?"
            declared = _declared_variant(text)
            flag = "" if detected == declared else "  ← 不一致（手臂会错位）"
            print("    皮肤模型：贴图=%s | 模块声明=%s%s"
                  % (detected, declared, flag))
        except Exception:
            pass


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
        mskin, variant = prepare_mascot_skin(skin_path)
        for rel in mascots:
            template = asar.read(rel)
            module = build_mascot_module(mskin, template, variant)
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

    # 入口处确保依赖就绪：缺 Pillow 就装，装不上再退，而不是等到裁图时才崩
    env_check.require_or_install('PIL', '裁切皮肤、生成贴图需要 Pillow')

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
