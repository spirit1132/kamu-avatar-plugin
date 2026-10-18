#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KAMUCL 一键美化 · 环境依赖检查与自动安装
==========================================

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

所有 Python 依赖的检查和安装都集中在这里，其它脚本已不再各自 print 提示后
直接 sys.exit。

设计要点
--------
* 只依赖标准库，import 本模块本身不会失败
* 检查项分级：
    - required : 缺了就无法工作（Pillow）
    - optional : 缺了只影响个别功能，给出提示但不阻断
* 安装方式按顺序尝试，直到成功：
    1. 当前解释器的 `python -m pip install --upgrade <包>`
    2. `--user` 模式（应对「全局站点包无写权限」）
    3. 国内镜像源（应对默认源超时/被墙）
    4. 若当前环境是 venv/conda 外的系统 Python，提示改用 `--user`
* 全程不联网做任何其它事，只在用户同意时才调用 pip
* 支持 --offline / --no-install 让用户完全掌控

用法
----
    python env_check.py                  # 检查并（询问后）安装缺失依赖
    python env_check.py --list           # 只看状态
    python env_check.py --yes            # 不问，直接装
    python env_check.py --no-install     # 只检查不安装
    python env_check.py --require pillow # 供其它脚本调用：确保可用，否则退出
"""

import argparse
import os
import subprocess
import sys

# ---------------------------------------------------------------- 依赖定义

# 包名 -> (import 名, 是否必需, 说明)
REQUIREMENTS = [
    ('Pillow', 'PIL', True, '图像处理（皮肤裁切、图标生成）'),
]

# 安装失败时依次尝试的额外源（仅在默认源失败后使用）
MIRRORS = [
    ('官方源', None),
    ('清华镜像', 'https://pypi.tuna.tsinghua.edu.cn/simple'),
    ('阿里云镜像', 'https://mirrors.aliyun.com/pypi/simple/'),
]

MIN_PYTHON = (3, 6)

C_OK = '\033[92m'
C_WARN = '\033[93m'
C_ERR = '\033[91m'
C_DIM = '\033[90m'
C_END = '\033[0m'

# 勾叉符号在 GBK 控制台下会 UnicodeEncodeError（如 chcp 936），
# 检测不到 UTF-8 输出能力时退化为 ASCII。
_MARKS = {'ok': '✓', 'warn': '!', 'err': '✗'}
_PLAIN = False


def _configure_console():
    """
    统一处理控制台编码与颜色：
      1. 尽量把 stdout 切到 UTF-8（Windows 上 reconfigure 可用）
      2. 切不动就把勾叉符号换成 ASCII，保证不崩
      3. 再决定是否启用 ANSI 颜色
    """
    global C_OK, C_WARN, C_ERR, C_DIM, C_END, _PLAIN

    # 1) 尝试切 UTF-8
    enc_ok = False
    try:
        enc = (getattr(sys.stdout, 'encoding', '') or '').lower()
        if 'utf' in enc:
            enc_ok = True
        else:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
            enc_ok = True
    except Exception:
        enc_ok = False

    # 2) 还是不行就用 ASCII
    if not enc_ok:
        _PLAIN = True
        _MARKS['ok'] = 'v'
        _MARKS['err'] = 'x'

    # 3) 颜色：非 Windows 默认支持；Windows 需要打开 VT 处理
    if os.name != 'nt':
        return
    if os.environ.get('WT_SESSION') or os.environ.get('TERM'):
        return
    try:
        import ctypes
        k = ctypes.WinDLL('kernel32')
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        C_OK = C_WARN = C_ERR = C_DIM = C_END = ''


def ok(msg):
    print('%s  %s %s%s' % (C_OK, _MARKS['ok'], msg, C_END))


def warn(msg):
    print('%s  %s %s%s' % (C_WARN, _MARKS['warn'], msg, C_END))


def err(msg):
    print('%s  %s %s%s' % (C_ERR, _MARKS['err'], msg, C_END))


def dim(msg):
    print('%s    %s%s' % (C_DIM, msg, C_END))


# ------------------------------------------------------------ Python 版本

def check_python():
    """返回 (是否满足, 当前版本字符串)。"""
    cur = sys.version_info[:3]
    text = '%d.%d.%d' % cur
    return cur[:2] >= MIN_PYTHON, text


# ------------------------------------------------------------ 依赖状态查询

def module_available(import_name):
    """只尝试 import，不执行任何副作用逻辑。"""
    try:
        __import__(import_name)
        return True
    except Exception:
        return False


def module_version(import_name):
    try:
        mod = __import__(import_name)
        v = getattr(mod, '__version__', None)
        if v:
            return str(v)
        # Pillow 老版本把版本放在 PIL.PILLOW_VERSION
        inner = getattr(mod, 'PILLOW_VERSION', None)
        return str(inner) if inner else '已安装'
    except Exception:
        return None


def survey():
    """返回 [(包名, import名, 必需, 说明, 是否已安装, 版本)]"""
    out = []
    for pkg, imp, required, desc in REQUIREMENTS:
        okk = module_available(imp)
        out.append((pkg, imp, required, desc, okk,
                    module_version(imp) if okk else None))
    return out


# ---------------------------------------------------------------- pip 调用

def _pip_cmd(args):
    """构造 pip 命令；优先用 `python -m pip`，避免 pip.exe 不在 PATH。"""
    return [sys.executable, '-m', 'pip'] + list(args)


def _run(cmd, timeout=600):
    """
    执行命令并实时回显 pip 输出。

    返回 (是否成功, 输出文本)。
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
        )
    except Exception as exc:
        return False, '无法启动 pip：%s' % exc

    lines = []
    try:
        for line in proc.stdout:
            lines.append(line)
            # 只回显关键行，避免刷屏
            s = line.rstrip('\n')
            if any(k in s for k in ('Collecting', 'Downloading', 'Installing',
                                    'Successfully', 'ERROR', 'error:',
                                    'Requirement already', 'WARNING: Retrying')):
                print('      %s' % s)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, 'pip 超时（%d 秒）' % timeout
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return False, 'pip 执行异常：%s' % exc

    return proc.returncode == 0, ''.join(lines)


def pip_available():
    """当前解释器是否能用 pip（优先 python -m pip，其次 pip.exe）。"""
    if module_available('pip'):
        return True
    try:
        r = subprocess.run(_pip_cmd(['--version']),
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def ensure_pip(quiet=False):
    """
    确保 pip 可用。缺失时尝试 ensurepip（Python 自带，无需联网）。

    返回 (是否可用, 说明)
    """
    if pip_available():
        return True, ''

    if not quiet:
        dim('pip 不可用，尝试用 ensurepip 修复 ...')
    try:
        r = subprocess.run([sys.executable, '-m', 'ensurepip',
                            '--upgrade', '--default-pip'],
                           capture_output=True, timeout=300,
                           universal_newlines=True,
                           encoding='utf-8', errors='replace')
    except Exception as exc:
        return False, 'ensurepip 执行异常：%s' % exc

    if pip_available():
        return True, ''

    detail = (r.stderr or r.stdout or '').strip().splitlines()
    return False, (detail[-1] if detail else 'ensurepip 未能修复 pip')


def install_package(pkg, quiet=False):
    """
    尝试安装一个包。依次尝试：默认源 → --user → 各镜像源。

    返回 (是否成功, 失败原因)
    """
    # 先确保 pip 存在，否则后面的尝试全是白费
    has_pip, why = ensure_pip(quiet=quiet)
    if not has_pip:
        return False, 'pip 不可用（%s）' % why

    attempts = []

    # 1) 常规安装
    attempts.append(('默认源', _pip_cmd(['install', '--upgrade', pkg])))
    # 2) 用户目录安装（全局站点包无写权限时）
    attempts.append(('--user', _pip_cmd(['install', '--upgrade', '--user', pkg])))

    # 3) 镜像源（默认源慢/不可达时）
    for label, url in MIRRORS[1:]:
        attempts.append((label,
                         _pip_cmd(['install', '--upgrade', '--user',
                                   '-i', url, pkg,
                                   '--trusted-host',
                                   url.split('//', 1)[-1].split('/', 1)[0]])))

    last_err = ''
    for label, cmd in attempts:
        if not quiet:
            dim('尝试：%s（%s）' % (label, ' '.join(cmd[3:5])))
        success, output = _run(cmd)
        if success:
            # pip 返回 0 仍要复查 import，防止装到了别的解释器
            if module_available(REQUIREMENTS_NAME_TO_IMPORT.get(pkg, pkg)):
                return True, ''
            last_err = 'pip 报告成功，但 import 仍失败（可能装到了其它解释器）'
            continue
        last_err = output.strip().splitlines()[-1] if output.strip() else '未知错误'
        # 离线 / 网络类错误重试镜像没意义，但保留尝试也无害
    return False, last_err


REQUIREMENTS_NAME_TO_IMPORT = {p: i for p, i, _, _ in REQUIREMENTS}


# ------------------------------------------------------------------ 交互

def ask(prompt, default=True):
    """简单 y/n 询问；非交互环境直接返回默认值。"""
    if not sys.stdin or not sys.stdin.isatty():
        return default
    suffix = ' [Y/n] ' if default else ' [y/N] '
    try:
        ans = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not ans:
        return default
    return ans in ('y', 'yes', '是', '好')


def ensure_requirements(auto_install=True, interactive=True, quiet=False):
    """
    检查并（按需）安装依赖。这是给其它脚本调用的主入口。

    参数
    ----
    auto_install : 允许调用 pip 安装
    interactive  : 是否询问用户（False 则视为同意）
    quiet        : 减少输出

    返回 True 表示所有**必需**依赖都就绪。
    """
    _configure_console()

    good, ver = check_python()
    if not good:
        err('Python 版本过低：%s（需要 %d.%d 或更高）' % (ver, *MIN_PYTHON))
        return False
    if not quiet:
        dim('Python %s（%s）' % (ver, sys.executable))

    items = survey()
    missing_required = [it for it in items if it[2] and not it[4]]
    missing_optional = [it for it in items if not it[2] and not it[4]]

    for pkg, imp, required, desc, present, version in items:
        if present:
            if not quiet:
                ok('%s %s（%s）' % (pkg, version, desc))
        else:
            tag = '必需' if required else '可选'
            warn('%s 未安装（%s，%s）' % (pkg, tag, desc))

    if not missing_required:
        return True

    if not auto_install:
        err('缺少必需依赖：%s' % '、'.join(it[0] for it in missing_required))
        dim('手动安装：%s -m pip install %s'
            % (sys.executable, ' '.join(it[0] for it in missing_required)))
        return False

    pkgs = [it[0] for it in missing_required]
    if interactive:
        print()
        if not ask('是否现在自动安装 %s？' % '、'.join(pkgs)):
            err('已取消。请手动执行：%s -m pip install %s'
                % (sys.executable, ' '.join(pkgs)))
            return False

    print()
    for name in pkgs:
        dim('正在安装 %s ...' % name)
        success, reason = install_package(name, quiet=quiet)
        if success:
            ok('%s 安装成功' % name)
        else:
            err('%s 安装失败：%s' % (name, reason))
            _print_manual_hint(name)
            return False

    # 装完复查
    still = [it[0] for it in survey() if it[2] and not it[4]]
    if still:
        err('安装后仍缺少：%s' % '、'.join(still))
        for name in still:
            _print_manual_hint(name)
        return False

    return True


def _print_manual_hint(pkg):
    print()
    err('请手动安装 %s，任选一条：' % pkg)
    dim('  %s -m pip install %s' % (sys.executable, pkg))
    dim('  %s -m pip install --user %s' % (sys.executable, pkg))
    dim('  %s -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple %s'
        % (sys.executable, pkg))
    print()
    dim('如果提示 "No module named pip"：')
    dim('  %s -m ensurepip --upgrade' % sys.executable)
    dim('若国内网络下载慢，可先设置镜像：')
    dim('  %s -m pip config set global.index-url '
        'https://pypi.tuna.tsinghua.edu.cn/simple' % sys.executable)


def require(import_name, hint=None):
    """
    供其它脚本使用：确保某个模块可用，否则打印提示并 sys.exit(1)。

    与直接 `from PIL import Image` 相比，这里会给出可执行的修复命令。
    不做安装（用于库函数内部，避免在深层调用里突然联网）。
    """
    if module_available(import_name):
        return True
    pkg = {i: p for p, i, _, _ in REQUIREMENTS}.get(import_name, import_name)
    _configure_console()
    err('缺少依赖：%s（import %s）' % (pkg, import_name))
    if hint:
        dim(hint)
    _print_manual_hint(pkg)
    sys.exit(1)


def require_or_install(import_name, hint=None):
    """
    供脚本入口使用：确保模块可用，缺了就尝试安装，仍失败才退出。

    与 require() 的区别是**会主动装**，适合直接双击/命令行运行的脚本。
    尊重环境变量 KAMUCL_NO_INSTALL=1 与 --no-install-deps 的语义。
    """
    if module_available(import_name):
        return True

    _configure_console()
    pkg = {i: p for p, i, _, _ in REQUIREMENTS}.get(import_name, import_name)
    err('缺少依赖：%s（import %s）' % (pkg, import_name))
    if hint:
        dim(hint)

    no_install = os.environ.get('KAMUCL_NO_INSTALL') == '1'
    if no_install:
        _print_manual_hint(pkg)
        sys.exit(1)

    # 非交互式（例如被 bat 调用且已确认过）直接装；交互式则先问
    interactive = not os.environ.get('KAMUCL_YES')
    auto = (not interactive) or ask('是否现在自动安装 %s？' % pkg)
    if not auto:
        _print_manual_hint(pkg)
        sys.exit(1)

    print()
    dim('正在安装 %s ...' % pkg)
    success, reason = install_package(pkg)
    if success and module_available(import_name):
        ok('%s 安装成功' % pkg)
        return True

    err('%s 安装失败：%s' % (pkg, reason))
    _print_manual_hint(pkg)
    sys.exit(1)


# -------------------------------------------------------------------- CLI

def cmd_list():
    _configure_console()
    print()
    good, ver = check_python()
    line = 'Python %s' % ver
    if good:
        ok(line)
    else:
        err('%s（需要 %d.%d+）' % (line, *MIN_PYTHON))
    dim('解释器：%s' % sys.executable)
    venv = (hasattr(sys, 'real_prefix')
            or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
    dim('虚拟环境：%s' % ('是' if venv else '否'))

    print()
    for pkg, imp, required, desc, present, version in survey():
        tag = '必需' if required else '可选'
        if present:
            ok('%s %s（%s，%s）' % (pkg, version, tag, desc))
        else:
            warn('%s 未安装（%s，%s）' % (pkg, tag, desc))

    # 顺带看 pip 本身是否可用
    print()
    if module_available('pip'):
        ok('pip 可用')
    else:
        warn('pip 不可用，可执行：%s -m ensurepip --upgrade' % sys.executable)


def main():
    _configure_console()
    ap = argparse.ArgumentParser(
        description='KAMUCL 一键美化 · 环境依赖检查与自动安装')
    ap.add_argument('--list', action='store_true', help='只查看依赖状态')
    ap.add_argument('--yes', '-y', action='store_true', help='不询问，直接安装')
    ap.add_argument('--no-install', action='store_true', help='只检查，不自动安装')
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return 0

    print()
    dim('正在检查运行环境 ...')
    print()
    good = ensure_requirements(
        auto_install=not args.no_install,
        interactive=not args.yes,
    )
    print()
    if good:
        ok('环境就绪')
        return 0
    err('环境不完整')
    return 1


if __name__ == '__main__':
    sys.exit(main())
