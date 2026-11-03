"""One isolated Edge PWA over Chromium's local JSON pipe; no embedding or cropping."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit


class WindowCaption:
    def __init__(self,profile):
        self.profile=Path(profile);self.process=None;self.target=None;self.session=None
        self.reader=None;self.writer=None;self.responses=queue.Queue();self.sequence=0
        self.lock=threading.Lock();self.command_lock=threading.Lock();self.overlay=False
        self.connected=threading.Event();self.hwnd=None;self.fallback=False

    def _find_edge(self):
        for variable in ('ProgramFiles(x86)','ProgramFiles','LOCALAPPDATA'):
            path=Path(os.environ.get(variable,'C:/'))/'Microsoft/Edge/Application/msedge.exe'
            if path.is_file():return path
        raise RuntimeError('记忆面板需要已安装的新版 Microsoft Edge；CLI/MCP 不受影响。')

    def _read(self,stream,responses):
        pending=bytearray()
        try:
            while True:
                chunk=stream.read(65536)
                if not chunk:break
                pending.extend(chunk)
                if len(pending)>16*1024*1024:raise ValueError('浏览器响应过长')
                while b'\0' in pending:
                    raw,_,rest=pending.partition(b'\0');pending=bytearray(rest)
                    message=json.loads(raw)
                    if 'id' in message:responses.put(message)
                    elif message.get('method')=='Target.targetDestroyed' and message.get('params',{}).get('targetId')==self.target:self.target=None
        except (OSError,ValueError):pass
        finally:
            if responses is self.responses:self.connected.clear()
            responses.put(None)

    def command(self,method,params=None,session=None,timeout=30):
        with self.command_lock:
            self.sequence+=1;ident=self.sequence
            packet={'id':ident,'method':method,'params':params or {}}
            active_session=session or self.session
            if active_session:packet['sessionId']=active_session
            if not self.writer:raise RuntimeError('浏览器连接尚未准备好')
            self.writer.write(json.dumps(packet).encode('utf-8')+b'\0')
            deadline=time.monotonic()+timeout
            while True:
                try:reply=self.responses.get(timeout=max(.01,deadline-time.monotonic()))
                except queue.Empty:raise RuntimeError('记忆窗口响应超时：'+method) from None
                if reply is None:raise RuntimeError('记忆窗口连接已关闭')
                if reply.get('id')!=ident:continue
                if 'error' in reply:
                    reason=re.sub(r'(?:https?|wss?)://\S+','[local-app]',reply['error'].get('message',''))
                    raise RuntimeError(method+'：'+reason)
                return reply.get('result',{})

    def _start(self):
        if os.name!='nt':raise RuntimeError('当前图形入口需要 Windows 和 Microsoft Edge；CLI/MCP 可独立使用。')
        import msvcrt
        browser=self._find_edge()
        self.profile.mkdir(parents=True,exist_ok=True)
        child_read,parent_write=os.pipe();parent_read,child_write=os.pipe()
        self.reader=os.fdopen(parent_read,'rb',buffering=0);self.writer=os.fdopen(parent_write,'wb',buffering=0)
        handles=[msvcrt.get_osfhandle(fd) for fd in (child_read,child_write)]
        for fd in (child_read,child_write):os.set_inheritable(fd,True)
        startup=subprocess.STARTUPINFO();startup.lpAttributeList={'handle_list':handles}
        args=[str(browser),'--user-data-dir='+str(self.profile),'--remote-debugging-pipe','--remote-debugging-io-pipes='+','.join(map(str,handles)),
              '--no-startup-window','--no-first-run','--no-default-browser-check','--disable-sync','--disable-background-mode','--disable-session-crashed-bubble','--window-size=1180,820']
        try:self.process=subprocess.Popen(args,startupinfo=startup,close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        finally:
            os.close(child_read);os.close(child_write)
        self.responses=queue.Queue();self.session=None
        self.connected.set()
        threading.Thread(target=self._read,args=(self.reader,self.responses),daemon=True,name='memory-app-pipe').start()
        self.session=self.command('Target.attachToBrowserTarget')['sessionId']
        self.command('Target.setDiscoverTargets',{'discover':True})

    def open(self,url):
        parts=urlsplit(url)
        if parts.scheme!='http' or parts.hostname!='127.0.0.1' or parts.path!='/':raise ValueError('仅允许本地记忆面板地址')
        base='http://127.0.0.1:'+str(parts.port)+'/'
        with self.lock:
            for attempt in range(2):
                try:return self._open(url,base)
                except (OSError,RuntimeError) as error:
                    if self.connected.is_set():raise
                    self.close()
                    if attempt:
                        try:return self._fallback_open(url)
                        except Exception as fallback_error:
                            raise RuntimeError('浏览器连接未建立，Edge 应用模式也无法打开；请从“打开记忆面板”重试。') from fallback_error

    def _fallback_open(self,url):
        browser=self._find_edge()
        self.profile.mkdir(parents=True,exist_ok=True)
        import ctypes
        args=['--user-data-dir='+str(self.profile),'--app='+url,'--no-first-run','--no-default-browser-check',
              '--disable-sync','--disable-background-mode','--disable-session-crashed-bubble']
        result=ctypes.windll.shell32.ShellExecuteW(None,'open',str(browser),subprocess.list2cmdline(args),str(self.profile),1)
        if result<=32:raise OSError('Edge 应用窗口启动失败，ShellExecute 代码 '+str(result))
        self.process=None
        self.fallback=True;self.target='edge-app'

    def _open(self,url,base):
        if self.fallback:
            self._fallback_open(url);return
        if self.process is None or self.process.poll() is not None or not self.connected.is_set():
            self.close()
            try:self._start()
            except Exception:self.close();raise
        if self.target:
            try:
                existing=self.command('Target.getTargetInfo',{'targetId':self.target})['targetInfo']
                if existing['url'].startswith(base):
                    window=self.command('Browser.getWindowForTarget',{'targetId':self.target})
                    if window['bounds']['windowState']=='minimized':self.command('Browser.setWindowBounds',{'windowId':window['windowId'],'bounds':{'windowState':'normal'}})
                    self.command('Target.activateTarget',{'targetId':self.target});return
            except RuntimeError:self.target=None
        self.command('PWA.install',{'manifestId':base,'installUrlOrBundleUrl':base})
        self.command('PWA.changeAppUserSettings',{'manifestId':base,'displayMode':'standalone'})
        self.target=self.command('PWA.launch',{'manifestId':base,'url':url})['targetId']
        from windows_taskbar import bind_window
        self.hwnd=bind_window(self.process.pid,self.profile.parent.parent)

    def close(self):
        if self.hwnd:
            from windows_taskbar import clear_window
            try:clear_window(self.hwnd,self.process.pid,self.profile.parent.parent)
            except OSError:pass
            self.hwnd=None
        if self.process and self.process.poll() is None:
            try:self.command('Browser.close',timeout=3)
            except (OSError,RuntimeError):pass
            try:self.process.wait(5)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(5)
        for stream in (self.writer,self.reader):
            if stream:
                try:stream.close()
                except OSError:pass
        self.writer=None;self.reader=None;self.target=None;self.session=None;self.fallback=False
        self.connected.clear()

    def status(self):
        opened=bool(self.target) and (self.connected.is_set() or self.fallback)
        return {'engine':'Microsoft Edge PWA','opened':opened,'fallback':self.fallback,'overlay':self.overlay}
