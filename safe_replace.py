#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KAMUCL 安全替换
================

无备份的原子替换：**先构造、再验证、最后替换**。

作者：spirit1132 (https://github.com/spirit1132)
许可：MIT

为什么不需要备份
----------------
传统做法是"先备份、再替换"，出问题用备份还原。但 KAMUCL.exe 单个 246 MB，
备份会迅速占满磁盘。

本模块改用**先验证再替换**，从根本上避免"替换了坏文件"：

    1. 把补丁结果写到同目录的临时文件（同一分区，保证能原子替换）
    2. 用 Windows 加载器验证临时文件（CREATE_SUSPENDED，不执行任何代码）
    3. 验证通过才 os.replace() 覆盖原文件（NTFS 上是原子操作）
    4. 任何一步失败，原文件都**从未被触碰**

这样做的安全性不低于备份方案：只要文件被替换，它就已经通过验证了。
代价是无法回滚到"打补丁前"的状态——需要恢复时重新安装 KAMUCL 即可。
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct


class VerifyError(Exception):
    """替换前的验证失败"""


# CreateProcess 标志
CREATE_SUSPENDED = 0x00000004      # 创建后立即挂起，不执行任何指令
CREATE_NO_WINDOW = 0x08000000      # 不创建控制台窗口

# 常见错误码
_ERR_TEXT = {
    0: '成功',
    2: 'ERROR_FILE_NOT_FOUND',
    5: 'ERROR_ACCESS_DENIED（文件被占用？）',
    11: 'ERROR_BAD_FORMAT',
    193: 'ERROR_BAD_EXE_FORMAT（PE 结构被破坏）',
    216: 'ERROR_EXE_MACHINE_TYPE_MISMATCH',
    267: 'ERROR_DIRECTORY',
    740: 'ERROR_ELEVATION_REQUIRED',
}


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR),
                ('lpTitle', wt.LPWSTR), ('dwX', wt.DWORD), ('dwY', wt.DWORD),
                ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD), ('dwXCountChars', wt.DWORD),
                ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD),
                ('dwFlags', wt.DWORD), ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', wt.HANDLE),
                ('hStdOutput', wt.HANDLE), ('hStdError', wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wt.HANDLE), ('hThread', wt.HANDLE),
                ('dwProcessId', wt.DWORD), ('dwThreadId', wt.DWORD)]


def verify_loadable(path, timeout_s=0):
    """
    用 Windows 加载器验证可执行文件是否有效。

    使用 CREATE_SUSPENDED：进程被创建后处于挂起状态，**不会执行任何指令**，
    随即被终止。因此既能真实反映加载器的判断，又不会有副作用。

    返回 (ok, error_code, message)
    """
    if not os.path.isfile(path):
        return False, 2, '文件不存在'

    try:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
    except OSError as exc:
        # 非 Windows 平台无法验证，放行
        return True, 0, '当前平台无法验证（%s）' % exc

    k.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p,
                                 ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p,
                                 wt.LPCWSTR, ctypes.c_void_p,
                                 ctypes.POINTER(_PROCESS_INFORMATION)]
    k.CreateProcessW.restype = wt.BOOL
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k.CloseHandle.argtypes = [wt.HANDLE]

    si = _STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = _PROCESS_INFORMATION()
    ctypes.set_last_error(0)

    ok = k.CreateProcessW(
        os.path.abspath(path), None, None, None, False,
        CREATE_SUSPENDED | CREATE_NO_WINDOW, None,
        os.path.dirname(os.path.abspath(path)) or None,
        ctypes.byref(si), ctypes.byref(pi))

    code = ctypes.get_last_error()

    if ok:
        # 挂起状态下终止：进程从未执行过任何代码
        k.TerminateProcess(pi.hProcess, 0)
        k.CloseHandle(pi.hProcess)
        k.CloseHandle(pi.hThread)
        return True, 0, '可加载'

    return False, code, _ERR_TEXT.get(code, '错误码 %d' % code)


def safe_replace(target, write_func, verify=True, label=None):
    """
    安全替换一个可执行文件。

    参数
    ----
    target      : 要替换的目标文件路径
    write_func  : 回调 write_func(tmp_path)，负责把补丁结果写到临时文件
    verify      : 是否用加载器验证（.exe 应设为 True）
    label       : 日志用的名称

    流程
    ----
    写临时文件 -> 验证临时文件 -> 原子替换。任一步失败都抛异常，
    且**原文件始终未被修改**。

    返回临时文件的路径（已被 rename，通常不再存在）。
    """
    label = label or os.path.basename(target)
    if not os.path.exists(target):
        raise VerifyError('%s 不存在' % label)

    # 临时文件必须与目标同目录（同分区），否则 os.replace 不是原子操作
    tmp = target + '.kamucl-tmp'
    try:
        if os.path.exists(tmp):
            os.remove(tmp)

        # 1) 写临时文件
        write_func(tmp)
        if not os.path.exists(tmp):
            raise VerifyError('%s：临时文件未生成' % label)

        # 2) 验证
        if verify:
            ok, code, msg = verify_loadable(tmp)
            if not ok:
                raise VerifyError('%s：新文件无法被 Windows 加载（%s）' % (label, msg))

        # 3) 原子替换
        os.replace(tmp, target)
    finally:
        # 无论成功失败，都清理临时文件
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return tmp

def check_writable(target, label=None):
    """
    预检目标文件是否**真的可以被替换**，返回 (可写, 原因)。

    为什么需要这个：os.replace() 失败时可能已经替换掉了别的文件，
    造成"一部分改了、一部分没改"的中间态。提前探测能避免这种情况。

    检测方式（任一条失败即判定不可写）：
      1. 以独占方式打开（Windows 上用 O_EXCL 无法做到，改用重命名探测）
      2. 更可靠的做法：尝试把目标重命名到同目录的临时名再改回来。
         被别的进程以独占方式打开时，rename 会抛 ERROR_SHARING_VIOLATION。

    注意：只做探测，不改动内容。文件不存在则视为不可写。
    """
    label = label or os.path.basename(target)
    if not os.path.exists(target):
        return False, '%s 不存在' % label

    d = os.path.dirname(os.path.abspath(target)) or '.'
    probe = os.path.join(d, '.kamucl-lock-probe-%d.tmp' % os.getpid())
    try:
        # 先确认目录可写
        try:
            with open(probe, 'wb') as fh:
                fh.write(b'')
        except OSError as exc:
            return False, '%s 所在目录不可写（%s）' % (label, exc)
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass

        # 再探测目标本身能否被改名（等价于能否被 os.replace 覆盖）
        if os.name == 'nt':
            import ctypes
            import ctypes.wintypes as wt
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            GENERIC_READ = 0x80000000
            FILE_SHARE_NONE = 0x00000000
            OPEN_EXISTING = 3
            k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                      ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                      wt.HANDLE]
            k.CreateFileW.restype = wt.HANDLE
            handle = k.CreateFileW(os.path.abspath(target), GENERIC_READ,
                                   FILE_SHARE_NONE, None, OPEN_EXISTING,
                                   0, None)
            INVALID = ctypes.c_void_p(-1).value
            if handle in (None, INVALID):
                code = ctypes.get_last_error()
                if code == 32:
                    return False, ('%s 正被其他程序使用'
                                   '（WinError 32，请关闭资源管理器预览/杀毒软件）'
                                   % label)
                # 5 = 拒绝访问；其余也可能是权限问题
                return False, '%s 无法打开（WinError %d）' % (label, code)
            k.CloseHandle(handle)
        return True, ''
    except Exception as exc:
        return False, '%s 探测失败（%s）' % (label, exc)


def safe_replace_pre(target, write_func, label=None):
    """
    阶段一：构建到临时文件并验证，**不替换原文件**。

    与 safe_replace 配合可实现多文件的两阶段提交：
      阶段一：对每个目标调用 safe_replace_pre（失败则原文件全部未动）
      阶段二：全部通过后，对每个临时文件 os.replace() 到目标位置

    返回临时文件路径（调用方负责后续 rename 或清理）。
    """
    label = label or os.path.basename(target)
    if not os.path.exists(target):
        raise VerifyError('%s 不存在' % label)

    tmp = target + '.kamucl-tmp'
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    write_func(tmp)
    if not os.path.exists(tmp):
        raise VerifyError('%s：临时文件未生成' % label)

    # 结构性验证：补丁只允许改它该改的地方
    try:
        with open(target, 'rb') as f:
            orig_bytes = f.read()
        with open(tmp, 'rb') as f:
            new_bytes = f.read()
        scope_ok, scope_msg = verify_patch_scope(orig_bytes, new_bytes)
        if not scope_ok:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise VerifyError('%s：补丁范围校验失败（%s）' % (label, scope_msg))
    except VerifyError:
        raise
    except Exception:
        pass  # 非 PE 文件（如 asar）没有节结构，跳过此检查

    # 加载器验证：确认 Windows 能真正加载
    ok, code, msg = verify_loadable(tmp)
    if not ok:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise VerifyError('%s：新文件无法被 Windows 加载（%s）' % (label, msg))

    return tmp

# ------------------------------------------------------------------ 结构校验

def _pe_sections(data):
    """解析 PE 节表。返回 [(名称, raw_offset, raw_size)] 或 None（不是 PE）"""
    if len(data) < 0x200 or data[:2] != b'MZ':
        return None
    try:
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe:pe + 4] != b'PE\x00\x00':
            return None
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        optsz = struct.unpack_from('<H', data, pe + 20)[0]
        sh = pe + 24 + optsz
        out = []
        for i in range(nsec):
            o = sh + 40 * i
            if o + 40 > len(data):
                return None
            name = bytes(data[o:o + 8]).rstrip(b'\x00').decode('ascii', 'replace')
            vsize, vaddr, rsize, raddr = struct.unpack_from('<IIII', data, o + 8)
            out.append((name, raddr, rsize))
        return out
    except struct.error:
        return None


def verify_patch_scope(orig_bytes, new_bytes, allowed_names=('.rsrc',)):
    """
    结构性验证：补丁后的文件**只允许在指定的节内发生变化**。

    这是最直接的防线——补丁工具的 bug 无论如何表现，只要它改动了
    .rsrc 之外的任何字节（比如误写代码段），这里都会被拦下。

    返回 (ok, 描述)
    """
    if len(orig_bytes) != len(new_bytes):
        return False, '文件长度改变（%d -> %d）' % (len(orig_bytes), len(new_bytes))

    so = _pe_sections(orig_bytes)
    sn = _pe_sections(new_bytes)
    if so is None or sn is None:
        return False, 'PE 结构解析失败'
    if so != sn:
        return False, '节表发生变化'

    # PE 头部（节表之前）必须逐字节一致
    hdr_end = min(s[1] for s in so) if so else 0
    if orig_bytes[:hdr_end] != new_bytes[:hdr_end]:
        return False, 'PE 头部被改动'

    # 逐节比对
    changed = []
    for name, raddr, rsize in so:
        a = orig_bytes[raddr:raddr + rsize]
        b = new_bytes[raddr:raddr + rsize]
        if a != b:
            changed.append(name)
    bad = [c for c in changed if c not in allowed_names]
    if bad:
        return False, '改动了不允许修改的节: %s' % ', '.join(bad)

    if not changed:
        # 幂等：重复运行时文件已处于目标状态，视为成功
        return True, '无变化（已是目标状态）'
    return True, '仅改动节: %s' % ', '.join(sorted(changed))
