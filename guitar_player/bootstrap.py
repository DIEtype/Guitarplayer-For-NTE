"""Request normal Windows UAC elevation for this application, once."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys


def ensure_administrator(entry,argv=None,executable=None,api=None):
    """True: continue here. False: elevated child launched; parent must exit."""
    argv=list(sys.argv[1:] if argv is None else argv)
    if os.name!='nt' or '--self-test' in argv or '--no-admin' in argv:return True
    api=api or C.WinDLL('shell32',use_last_error=True)
    api.IsUserAnAdmin.argtypes=[];api.IsUserAnAdmin.restype=W.BOOL
    if api.IsUserAnAdmin():return True
    if '--admin-requested' in argv:raise RuntimeError('管理员启动未成功，已停止重复申请权限；请检查 Windows 账户权限')
    entry=Path(entry).resolve();python=Path(executable or sys.executable).resolve()
    frozen=getattr(sys,'frozen',False)
    pythonw=python.with_name('pythonw.exe')
    if not frozen and pythonw.is_file():python=pythonw
    arguments=subprocess.list2cmdline([*(() if frozen else (str(entry),)),*argv,'--admin-requested'])
    api.ShellExecuteW.argtypes=[W.HWND,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,C.c_int]
    api.ShellExecuteW.restype=C.c_void_p
    result=api.ShellExecuteW(None,'runas',str(python),arguments,str(python.parent if frozen else entry.parent),1 if frozen or python.name.lower()=='pythonw.exe' else 0)
    if not result or result<=32:
        raise RuntimeError(f'管理员启动被取消或未能完成（Windows 返回 {result or 0}）。请重新启动并在权限确认中选择“是”')
    return False
