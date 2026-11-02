"""Opt-in integration check against an actual, isolated browser app window."""
import ctypes
import json
from ctypes import wintypes as w
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from gui import create_server
from store import VERSION
from windows_taskbar import _properties,app_id


def capture(hwnd):
    from PIL import Image
    user=ctypes.WinDLL('user32',use_last_error=True);gdi=ctypes.WinDLL('gdi32')
    user.SetProcessDpiAwarenessContext.argtypes=[w.HANDLE]
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.GetWindowDC.argtypes=[w.HWND];user.GetWindowDC.restype=w.HDC
    user.ReleaseDC.argtypes=[w.HWND,w.HDC]
    user.PrintWindow.argtypes=[w.HWND,w.HDC,w.UINT]
    gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
    gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=w.HBITMAP
    gdi.SelectObject.argtypes=[w.HDC,w.HGDIOBJ];gdi.SelectObject.restype=w.HGDIOBJ
    gdi.DeleteObject.argtypes=[w.HGDIOBJ];gdi.DeleteDC.argtypes=[w.HDC]
    class Header(ctypes.Structure):
        _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bits',w.WORD),('compression',w.DWORD),('image_size',w.DWORD),('x',w.LONG),('y',w.LONG),('used',w.DWORD),('important',w.DWORD)]
    gdi.GetDIBits.argtypes=[w.HDC,w.HBITMAP,w.UINT,w.UINT,ctypes.c_void_p,ctypes.c_void_p,w.UINT]
    rect=w.RECT();user.GetWindowRect(hwnd,ctypes.byref(rect))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    source=user.GetWindowDC(hwnd);memory=gdi.CreateCompatibleDC(source)
    bitmap=gdi.CreateCompatibleBitmap(source,width,height);prior=gdi.SelectObject(memory,bitmap)
    try:
        if not user.PrintWindow(hwnd,memory,2):raise OSError('PrintWindow failed')
        header=Header();header.size=ctypes.sizeof(header);header.width=width;header.height=-height;header.planes=1;header.bits=32
        buffer=ctypes.create_string_buffer(width*height*4)
        if not gdi.GetDIBits(memory,bitmap,0,height,buffer,ctypes.byref(header),0):raise OSError('GetDIBits failed')
        return Image.frombytes('RGB',(width,height),buffer.raw,'raw','BGRX')
    finally:
        gdi.SelectObject(memory,prior);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,source)


def capture_owned(pid,hwnd,identity,destination):
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    owner=w.DWORD();kind=ctypes.create_unicode_buffer(128)
    user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner));user.GetClassNameW(hwnd,kind,len(kind))
    if owner.value!=pid or kind.value!='Chrome_WidgetWin_1' or _properties(hwnd=hwnd)[5]!=identity:
        raise AssertionError('Isolated app window identity changed')
    shot=capture(hwnd);shot.save(destination)
    # Sample the complete window, including the area occupied by the old native title bar.
    return {'top':'#'+bytes(shot.getpixel((shot.width//2,25))).hex(),'toolbar':'#'+bytes(shot.getpixel((shot.width//2,65))).hex(),'width':shot.width,'height':shot.height,'cropped':False}


@unittest.skipUnless(os.environ.get('RUN_NATIVE_WINDOW_TEST')=='1' and os.name=='nt','opt-in Windows desktop test')
class NativeWindowTests(unittest.TestCase):
    def test_pwa_full_window_and_controls(self):
        with tempfile.TemporaryDirectory(prefix='memory-native-pwa-',ignore_cleanup_errors=True) as root:
            server=create_server(root);server.admin.preferences({'theme':'dark'})
            # PWA install/uninstall must never share the user's real shortcut name.
            original=server.RequestHandlerClass.respond
            def respond(handler,status,value,json_body=True):
                if handler.path=='/manifest.webmanifest' and isinstance(value,dict):value={**value,'name':'Codex Memory Native Test '+str(server.server_port)}
                return original(handler,status,value,json_body)
            server.RequestHandlerClass.respond=respond
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)+'/'
            app=server.caption;page_session=None
            def evaluate(expression):
                result=app.command('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True},session=page_session)
                if result.get('exceptionDetails'):raise AssertionError(result['exceptionDetails'])
                return result['result'].get('value')
            def until(expression,seconds=10):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    if evaluate(expression):return
                    time.sleep(.05)
                raise AssertionError('Timed out: '+expression)
            def frame(name,color):
                destination=Path(os.environ.get('NATIVE_CAPTURE_DIR',str(Path(__file__).parent/'assets')))/('native-window-pwa-'+name+'.png')
                result=capture_owned(app.process.pid,app.hwnd,app_id(root),destination)
                self.assertFalse(result['cropped']);self.assertEqual(result['top'],color);self.assertEqual(result['toolbar'],color)
            try:
                app.open(base+'#'+server.token)
                pages=[item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page' and item['url'].startswith(base)]
                self.assertEqual(len(pages),1)
                page_session=app.command('Target.attachToTarget',{'targetId':pages[0]['targetId'],'flatten':True})['sessionId']
                until("document.querySelector('#app-version')?.textContent.includes("+json.dumps(VERSION)+") && navigator.windowControlsOverlay?.visible && document.documentElement.dataset.overlay==='true'")
                taskbar=_properties(hwnd=app.hwnd)
                self.assertEqual(taskbar[5],app_id(root))
                self.assertIn('pythonw.exe',taskbar[2])
                self.assertIn(str(Path(root).resolve()/'gui.py').casefold(),taskbar[2].casefold())
                self.assertNotIn('msedge',taskbar[2])
                title='Codex Memory · 本地记忆 [native-test-'+str(time.time_ns())+']'
                evaluate('document.title='+json.dumps(title))
                pages=[item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page']
                self.assertEqual(len(pages),1,'Startup opened a regular browser window')
                self.assertEqual(evaluate("document.querySelectorAll('#window-controls').length"),0)
                for theme,color in [('dark','#363847'),('light','#f2f1f6'),('dark','#363847')]:
                    if evaluate('document.documentElement.dataset.theme')!=theme:evaluate("document.getElementById('theme-toggle').click()")
                    until('document.documentElement.dataset.theme==='+json.dumps(theme))
                    until("!document.getAnimations().some(effect=>effect.playState==='running')")
                    frame(theme,color)
                for zoom in [75,100,125,150,100]:
                    evaluate("document.getElementById('settings-open').click()")
                    until("document.getElementById('settings').open")
                    evaluate("(()=>{const input=document.getElementById('zoom-range');input.value="+json.dumps(str(zoom))+";input.dispatchEvent(new Event('input'));input.dispatchEvent(new Event('change'));document.querySelector('#settings [data-close]').click();})()")
                    until("!document.getElementById('settings').open")
                    until("document.getElementById('add-note').getBoundingClientRect().right<=navigator.windowControlsOverlay.getTitlebarAreaRect().right")
                    self.assertEqual(evaluate("getComputedStyle(document.getElementById('theme-toggle')).webkitAppRegion"),'no-drag')
                window=app.command('Browser.getWindowForTarget',{'targetId':app.target})
                for state in ['maximized','normal','minimized']:
                    app.command('Browser.setWindowBounds',{'windowId':window['windowId'],'bounds':{'windowState':state}})
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        if app.command('Browser.getWindowBounds',{'windowId':window['windowId']})['bounds']['windowState']==state:break
                        time.sleep(.1)
                    else:self.fail('Window state did not change to '+state)
                    if state!='minimized':
                        until('navigator.windowControlsOverlay.visible')
                        time.sleep(.2);frame(state,'#363847')
                target=app.target;app.open(base+'#'+server.token)
                self.assertEqual(app.target,target,'Reopening created a new page')
                self.assertEqual(app.command('Browser.getWindowBounds',{'windowId':window['windowId']})['bounds']['windowState'],'normal')
                self.assertEqual(len([item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page']),1)
                app.command('Target.closeTarget',{'targetId':app.target})
                app.open(base+'#'+server.token)
                self.assertNotEqual(app.target,target,'Closing and reopening retained a stale target')
                self.assertEqual(len([item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page']),1)
                app.close();app.open(base+'#'+server.token)
                self.assertEqual(len([item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page']),1,'Browser restart restored extra windows')
                for _ in range(3):
                    app.command('Target.closeTarget',{'targetId':app.target});app.open(base+'#'+server.token)
                    self.assertEqual(len([item for item in app.command('Target.getTargets')['targetInfos'] if item['type']=='page']),1)
            finally:
                if app.target:
                    try:app.command('PWA.uninstall',{'manifestId':base})
                    except RuntimeError:pass
                app.close();server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main(verbosity=2)
