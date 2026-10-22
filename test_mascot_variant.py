#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
吉祥物皮肤模型类型（slim / classic）回归测试
============================================

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

覆盖点：
  1. detect_skin_variant 与 KAMUCL 的 detectSkinVariant 算法一致
  2. 高清皮肤缩放后仍能正确判定
  3. slim / classic 双向切换、幂等、无残留
  4. 生成的模块仍是合法 JS（调用 node --check）
  5. 在真实 app.asar 副本上端到端跑通，且只改目标模块

运行：
    python test_mascot_variant.py
    python test_mascot_variant.py --asar "C:\\path\\to\\app.asar"   # 额外做真机 asar 测试
"""

import argparse
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 依赖检查与自动安装统一走 env_check
import env_check

env_check.require_or_install('PIL', '回归测试需要 Pillow 生成测试皮肤')

from PIL import Image

try:
    import patch_splash_avatar as P
except Exception as exc:
    print('无法导入 patch_splash_avatar.py：%s' % exc, file=sys.stderr)
    sys.exit(1)


_passed = 0
_failed = 0


def check(name, cond, detail=''):
    global _passed, _failed
    if cond:
        _passed += 1
        print('  ✓ %s' % name)
    else:
        _failed += 1
        print('  ✗ %s%s' % (name, ('  -> ' + detail) if detail else ''))


def make_skin(slim, scale=1, base=(40, 50, 60, 255)):
    """
    构造一张测试皮肤。

    slim / classic 的差别只在 (54,20..31) 这一列：
      classic 有内容，slim 留空（这正是 KAMUCL detectSkinVariant 的判据）。
    """
    n = 64 * scale
    im = Image.new('RGBA', (n, n), base)
    if slim:
        for y in range(20 * scale, 32 * scale):
            for x in range(54 * scale, 55 * scale):
                im.putpixel((x, y), (0, 0, 0, 0))
    else:
        for y in range(20 * scale, 32 * scale):
            for x in range(54 * scale, 55 * scale):
                im.putpixel((x, y), (200, 180, 150, 255))
    return im


def section(title):
    print('\n── %s ──' % title)


# ---------------------------------------------------------------- 各测试段

def test_detect(tmp):
    section('1. detect_skin_variant 算法')

    check('标准 slim', P.detect_skin_variant(make_skin(True)) == 'slim')
    check('标准 classic', P.detect_skin_variant(make_skin(False)) == 'classic')

    # 末格有一点点颜色也要判 classic（KAMUCL 是「遇到不透明就 return classic」）
    im = make_skin(True)
    im.putpixel((54, 31), (1, 2, 3, 255))
    check('slim 末格有色 -> classic', P.detect_skin_variant(im) == 'classic')

    # 全透明图：该列全透明，按算法判 slim
    blank = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    check('全透明 -> slim', P.detect_skin_variant(blank) == 'slim')

    # 高清皮肤：探测前会缩放到 64x64，结论必须一致
    for scale in (2, 4, 8):
        s = P.detect_skin_variant(make_skin(True, scale))
        c = P.detect_skin_variant(make_skin(False, scale))
        check('%dx%d slim' % (64 * scale, 64 * scale), s == 'slim', s)
        check('%dx%d classic' % (64 * scale, 64 * scale), c == 'classic', c)


def _b64(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _template_legacy(skin_b64):
    """1.1.21 等未压缩版本：变量名可读。"""
    return ('''
const skinUrl = "data:image/png;base64,%s";
const palmUrl = "data:image/png;base64,%s";
const printUrl = "data:image/png;base64,%s";
function boot() {
    player = new PreviewPlayer();
        player.skin.map = skin;
        player.skin.setOuterLayerVisible(false);
}
''' % (skin_b64, _b64(Image.new('RGBA', (15, 15), (1, 2, 3, 255))),
       _b64(Image.new('RGBA', (15, 15), (4, 5, 6, 255))))).encode()


def _template_mangled(skin_b64):
    """1.1.48 等压缩版本：变量名被 mangle 成单字母，赋值用逗号串联。"""
    return ('''
const La="data:image/png;base64,%s",Da="data:image/png;base64,%s",Ra="data:image/png;base64,%s";
function boot(){h=new da,h.skin.map=b,h.skin.setOuterLayerVisible(!1),h.skin.position.y=16.8;}
''' % (skin_b64, _b64(Image.new('RGBA', (15, 15), (1, 2, 3, 255))),
       _b64(Image.new('RGBA', (15, 15), (4, 5, 6, 255))))).encode()


def test_module_switch(tmp):
    section('2. 模块内模型类型写入 / 切换')

    slim_dir = os.path.join(tmp, 'slim.png')
    cls_dir = os.path.join(tmp, 'classic.png')
    make_skin(True).save(slim_dir)
    make_skin(False).save(cls_dir)

    png_s, var_s = P.prepare_mascot_skin(slim_dir)
    png_c, var_c = P.prepare_mascot_skin(cls_dir)
    check('prepare 判定 slim', var_s == 'slim', var_s)
    check('prepare 判定 classic', var_c == 'classic', var_c)

    results = {}
    for style, mk in (('1.1.21 风格', _template_legacy),
                      ('1.1.48 风格', _template_mangled)):
        template = mk(_b64(make_skin(False)))
        mod_s = P.build_mascot_module(png_s, template, var_s)
        mod_c = P.build_mascot_module(png_c, mod_s, var_c)
        results[style] = (mod_s, mod_c)

        check('%s slim 写入一条声明' % style,
              mod_s.count(b'.skin.modelType') == 1,
              str(mod_s.count(b'.skin.modelType')))
        check('%s slim 声明值正确' % style,
              P._declared_variant(mod_s.decode()) == 'slim')
        check('%s slim 幂等' % style,
              P.build_mascot_module(png_s, mod_s, 'slim') == mod_s)
        check('%s classic 覆盖 slim' % style,
              P._declared_variant(mod_c.decode()) == 'classic')
        check('%s classic 后无 slim 残留' % style, b'"slim"' not in mod_c)
        check('%s classic 仍只一条声明' % style,
              mod_c.count(b'.skin.modelType') == 1)
        check('%s 再切回 slim' % style,
              P._declared_variant(
                  P.build_mascot_module(png_s, mod_c, 'slim').decode()) == 'slim')

    # 两种风格的语义必须一致
    check('两种风格都只改皮肤，小图保留',
          all(len(P.DATA_URL_RE.findall(m.decode())) == 3
              for pair in results.values() for m in pair))

    mod_s, mod_c = results['1.1.21 风格']
    return mod_s, mod_c


def test_js_syntax(tmp, mod_s, mod_c):
    section('3. 生成模块的 JS 语法')

    node = shutil.which('node')
    if not node:
        print('  (跳过：未找到 node)')
        return

    for name, mod in (('slim', mod_s), ('classic', mod_c)):
        f = os.path.join(tmp, 'mod_%s.mjs' % name)
        with open(f, 'wb') as fh:
            fh.write(mod)
        r = subprocess.run([node, '--check', f],
                           capture_output=True, timeout=60)
        check('node --check %s' % name, r.returncode == 0,
              r.stderr.decode('utf-8', 'replace')[:200])


def test_real_asar(asar_path, tmp):
    section('4. 真实 app.asar 副本')

    work = os.path.join(tmp, 'work.asar')
    shutil.copy2(asar_path, work)

    def snapshot(path):
        """
        读出一份「路径 -> 内容」快照。

        必须在调用 replace_many 之前完成：Asar.replace_many 会就地改写
        self.header 里的 offset/size，写完之后同一个 Asar 对象就读不出旧内容了。
        """
        a = P.Asar(path)
        out = {}

        def walk(node, rel):
            for k, v in (node.get('files') or {}).items():
                cur = rel + '/' + k if rel else k
                if 'files' in v:
                    walk(v, cur)
                elif 'offset' in v:
                    out[cur] = a.read(cur)

        walk(a.header, '')
        return out, a.total_size

    old_files, old_size = snapshot(work)

    a = P.Asar(work)
    mascots = a.list_mascot_modules()
    faces = a.list_splash_faces()
    if not mascots:
        print('  (跳过：asar 内没有 MascotStage 模块)')
        return
    check('找到 MascotStage 模块', bool(mascots))
    check('原版模块未声明模型类型',
          P._declared_variant(old_files[mascots[0]].decode()) == 'classic')

    slim_path = os.path.join(tmp, 'real_slim.png')
    make_skin(True).save(slim_path)
    png, var = P.prepare_mascot_skin(slim_path, 'slim')

    repl = {m: P.build_mascot_module(png, a.read(m), var) for m in mascots}
    if faces:
        repl[faces[0]] = P.build_face_module(P.prepare_avatar(slim_path),
                                             a.read(faces[0]))

    out = os.path.join(tmp, 'patched.asar')
    a.replace_many(repl, out)

    b = P.Asar(out)
    import hashlib
    for rel in repl:
        got = hashlib.sha256(b.read(rel)).hexdigest()
        check('完整性字段 %s' % rel.split('/')[-1],
              got == b.find(rel)['integrity']['hash'])

    # 用「重新打开」的方式读新包，避免受 replace_many 污染
    new_files, new_size = snapshot(out)
    check('文件数不变', len(old_files) == len(new_files),
          '%d -> %d' % (len(old_files), len(new_files)))
    check('文件顺序不变', list(old_files) == list(new_files))

    changed = [k for k in old_files if old_files[k] != new_files.get(k)]
    unexpected = [c for c in changed if c not in repl]
    check('只有目标模块被改动', not unexpected, str(unexpected[:5]))
    check('总大小变化等于目标模块变化量',
          new_size - old_size == sum(len(repl[k]) - len(old_files[k])
                                     for k in repl),
          '%+d' % (new_size - old_size))

    # 吉祥物模块里另外两张小图必须原样保留
    m = mascots[0]
    for key in ('palmUrl', 'printUrl'):
        old = re.search(key + r' ?= ?"data:image/png;base64,([A-Za-z0-9+/=]+)',
                        old_files[m].decode()).group(1)
        new = re.search(key + r' ?= ?"data:image/png;base64,([A-Za-z0-9+/=]+)',
                        new_files[m].decode()).group(1)
        check('%s 未变' % key, old == new)

    check('补丁后声明为 slim',
          P._declared_variant(new_files[m].decode()) == 'slim')


def test_e2e(asar_path, tmp):
    section('5. install.py 端到端')

    root = os.path.join(tmp, 'KAMUCL-runtime')
    ver = os.path.join(root, '1.1.21-test')
    res = os.path.join(ver, 'resources')
    os.makedirs(res, exist_ok=True)
    shutil.copy2(asar_path, os.path.join(res, 'app.asar'))
    # install.py 靠「版本文件夹里有 KAMUCL.exe」来识别安装目录，
    # 没有真实 exe 时放一个同名占位文件即可（不会被读取，只用于定位）。
    exe = os.path.join(ver, 'KAMUCL.exe')
    if not os.path.isfile(exe):
        with open(exe, 'wb') as fh:
            fh.write(b'MZ')

    plugin_dir = os.path.join(root, 'kamu_avatar_plugin')
    os.makedirs(plugin_dir, exist_ok=True)
    for f in ('install.py', 'patch_icons.py', 'patch_splash_avatar.py',
              'safe_replace.py', 'env_check.py', 'main.js', 'plugin.json'):
        src = os.path.join(HERE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(plugin_dir, f))

    slim_path = os.path.join(tmp, 'e2e_slim.png')
    make_skin(True).save(slim_path)
    cls_path = os.path.join(tmp, 'e2e_classic.png')
    make_skin(False).save(cls_path)

    py = sys.executable
    env = dict(os.environ, PYTHONIOENCODING='utf-8')

    def run(args):
        return subprocess.run([py, 'install.py'] + args,
                              cwd=plugin_dir, capture_output=True,
                              env=env, timeout=300)

    r = run(['--no-icon', '--no-plugin', '--skin', slim_path])
    out = r.stdout.decode('utf-8', 'replace')
    check('slim 端到端执行成功', r.returncode == 0,
          r.stderr.decode('utf-8', 'replace')[-300:])
    check('输出提到 slim', 'slim' in out, out[-300:])

    r = run(['--no-icon', '--no-plugin', '--skin', cls_path])
    out2 = r.stdout.decode('utf-8', 'replace')
    check('classic 覆盖执行成功', r.returncode == 0,
          r.stderr.decode('utf-8', 'replace')[-300:])
    check('输出提到 classic', 'classic' in out2)

    r = run(['--no-icon', '--no-plugin', '--skin', cls_path, '--list'])
    listing = r.stdout.decode('utf-8', 'replace')
    check('--list 报告贴图与声明一致',
          '贴图=classic | 模块声明=classic' in listing, listing[-400:])


def main():
    ap = argparse.ArgumentParser(
        description='吉祥物 slim / classic 模型类型回归测试')
    ap.add_argument('--asar', help='真实 app.asar 路径，额外做副本端到端测试')
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='kamu_variant_')
    print('临时目录：%s' % tmp)
    try:
        test_detect(tmp)
        mod_s, mod_c = test_module_switch(tmp)
        test_js_syntax(tmp, mod_s, mod_c)
        if args.asar:
            if os.path.isfile(args.asar):
                test_real_asar(args.asar, tmp)
                test_e2e(args.asar, tmp)
            else:
                print('\n指定的 asar 不存在，跳过真机测试：%s' % args.asar)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 46)
    print('通过 %d 项，失败 %d 项' % (_passed, _failed))
    return 1 if _failed else 0


if __name__ == '__main__':
    sys.exit(main())
