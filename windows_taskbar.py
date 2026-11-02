"""Windows taskbar identity and relaunch properties for this app's own windows."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import subprocess
import sys
import time
import uuid


def app_id(root):
    return 'CodexMemory.'+hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:16]


def _properties(*, hwnd=None, path=None, values=None):
    class Key(C.Structure):
        _fields_=[('format',C.c_ubyte*16),('pid',W.DWORD)]
    class Value(C.Union):
        _fields_=[('string',W.LPWSTR),('pointer',C.c_void_p),('record',C.c_void_p*2),('number',C.c_longlong)]
    class Variant(C.Structure):
        _anonymous_=('value',)
        _fields_=[('vt',W.WORD),('reserved',W.WORD*3),('value',Value)]
    def guid(value):return (C.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le)
    def check(code):
        if code<0:raise OSError(code,'无法设置本应用的任务栏启动入口')
    def method(index,restype,*args):
        table=C.cast(store,C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(restype,C.c_void_p,*args)(table[index])
    shell=C.WinDLL('shell32');ole=C.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[C.c_void_p,W.DWORD];ole.CoInitializeEx.restype=C.c_long
    initialized=ole.CoInitializeEx(None,0)>=0
    store=C.c_void_p();iid=guid('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    try:
        if hwnd is not None:
            shell.SHGetPropertyStoreForWindow.argtypes=[W.HWND,C.c_void_p,C.POINTER(C.c_void_p)]
            check(shell.SHGetPropertyStoreForWindow(hwnd,C.byref(iid),C.byref(store)))
        else:
            shell.SHGetPropertyStoreFromParsingName.argtypes=[W.LPCWSTR,C.c_void_p,W.DWORD,C.c_void_p,C.POINTER(C.c_void_p)]
            check(shell.SHGetPropertyStoreFromParsingName(str(path),None,2 if values else 0,C.byref(iid),C.byref(store)))
        ole.CoTaskMemAlloc.argtypes=[C.c_size_t];ole.CoTaskMemAlloc.restype=C.c_void_p
        ole.PropVariantClear.argtypes=[C.POINTER(Variant)]
        result={}
        for pid in (2,3,4,5):
            key=Key(guid('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'),pid);value=Variant()
            try:
                if values is not None and pid in values:
                    if values[pid] is not None:
                        text=C.create_unicode_buffer(values[pid]);pointer=ole.CoTaskMemAlloc(C.sizeof(text))
                        if not pointer:raise MemoryError('任务栏属性内存分配失败')
                        C.memmove(pointer,text,C.sizeof(text));value.vt=31;value.pointer=pointer
                    check(method(6,C.c_long,C.POINTER(Key),C.POINTER(Variant))(store,C.byref(key),C.byref(value)))
                else:
                    check(method(5,C.c_long,C.POINTER(Key),C.POINTER(Variant))(store,C.byref(key),C.byref(value)))
                    result[pid]=value.string if value.vt==31 else None
            finally:ole.PropVariantClear(C.byref(value))
        if values is not None and path is not None:check(method(7,C.c_long)(store))
        return result
    finally:
        if store:method(2,W.ULONG)(store)
        if initialized:ole.CoUninitialize()


def bind_window(pid,root):
    root=Path(root).resolve();user=C.WinDLL('user32')
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=[callback,W.LPARAM]
    user.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    found=[]
    @callback
    def visit(hwnd,_):
        owner=W.DWORD();kind=C.create_unicode_buffer(80);title=C.create_unicode_buffer(512)
        user.GetWindowThreadProcessId(hwnd,C.byref(owner))
        if owner.value==pid:
            user.GetClassNameW(hwnd,kind,len(kind));user.GetWindowTextW(hwnd,title,len(title))
            if kind.value=='Chrome_WidgetWin_1' and title.value.startswith('Codex Memory'):found.append(hwnd)
        return True
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        found.clear();user.EnumWindows(visit,0)
        if len(found)==1:break
        time.sleep(.05)
    if len(found)!=1:raise RuntimeError('尚未找到本应用的唯一窗口，请重新打开面板')
    command=subprocess.list2cmdline([str(Path(sys.executable).with_name('pythonw.exe')),'-X','utf8',str(root/'gui.py'),'--root',str(root)])
    _properties(hwnd=found[0],values={2:command,3:str(root/'assets/icon.ico')+',0',4:'Codex Memory',5:app_id(root)})
    return found[0]


def clear_window(hwnd,pid,root):
    user=C.WinDLL('user32');owner=W.DWORD()
    user.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user.GetWindowThreadProcessId(hwnd,C.byref(owner))
    if owner.value==pid and _properties(hwnd=hwnd).get(5)==app_id(root):
        _properties(hwnd=hwnd,values={2:None,3:None,4:None,5:None})
