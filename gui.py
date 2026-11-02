"""Loopback-only memory manager and a console-free desktop entry point."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from store import MemoryStore, MemoryFault, VERSION, atomic, tokens, utc
from native_caption import WindowCaption


class MemoryAdmin:
    def __init__(self, root):
        self.store = MemoryStore(root)

    def preferences(self, patch=None):
        defaults={'theme':'system','accent':'#7866c5','motion':True,'zoom':100,'sort':'updated'}
        def validate(value):
            if not isinstance(value,dict) or set(value)-set(defaults):
                raise MemoryFault('invalid','外观设置包含未知字段')
            if 'theme' in value and value['theme'] not in ('light','dark','system'):
                raise MemoryFault('invalid','外观模式无效')
            if 'accent' in value and (not isinstance(value['accent'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value['accent'])):
                raise MemoryFault('invalid','主题色应为六位十六进制颜色，例如 #7866c5')
            if 'motion' in value and type(value['motion']) is not bool:
                raise MemoryFault('invalid','动效开关必须是布尔值')
            if 'zoom' in value and (type(value['zoom']) is not int or not 75<=value['zoom']<=150):
                raise MemoryFault('invalid','界面缩放应在75%至150%之间')
            if 'sort' in value and value['sort'] not in ('updated','created','importance','title'):
                raise MemoryFault('invalid','排序方式无效')
        if patch is not None:validate(patch)
        with self.store.locked():
            path=self.store._safe(self.store.data/'ui-settings.json')
            current={}
            if path.exists():
                try:
                    if path.stat().st_size>4096:raise ValueError()
                    current=json.loads(path.read_text(encoding='utf-8'))
                    validate(current)
                except (ValueError,TypeError):
                    raise MemoryFault('damaged','外观设置文件无效，原文件已保留') from None
            result={**defaults,**current,**(patch or {})}
            result['accent']=result['accent'].lower()
            if patch is not None:atomic(path,json.dumps(result,ensure_ascii=False,indent=2))
            return result

    def catalog(self, query='', view='all', project='', offset=0, sort='updated'):
        orders={'updated':'n.updated DESC,n.id','created':"json_extract(n.doc,'$.created_at') DESC,n.id",
                'importance':'n.importance DESC,n.updated DESC,n.id','title':"json_extract(n.doc,'$.title') COLLATE NOCASE,n.id"}
        if sort not in orders:raise MemoryFault('invalid','排序方式无效')
        if view not in ('all','global','project','muted','provisional') or len(query)>400 or not 0<=offset<=1000000:
            raise MemoryFault('invalid','筛选条件无效')
        with self.store.locked(), contextlib.closing(self.store._open_index()) as con:
            self.store._sync(con)
            where, args = ['1=1'], []
            if view in ('global','project'):
                where.append('n.scope=?');args.append(view)
            if view=='muted':where.append('n.muted=1')
            if view=='provisional':where.append("n.status='provisional'")
            if project:where.append('n.project=?');args.append(project)
            words=sorted(tokens(query),key=lambda w:(-len(w),w))[:48]
            table='notes n'
            order=orders[sort]
            if words:
                table='search JOIN notes n ON search.id=n.id';where.append('search MATCH ?');args.append(' OR '.join('"'+w+'"' for w in words))
            clause=' AND '.join(where)
            total=con.execute(f'SELECT count(*) FROM {table} WHERE {clause}',args).fetchone()[0]
            rows=con.execute(f'SELECT n.doc FROM {table} WHERE {clause} ORDER BY {order} LIMIT 50 OFFSET ?',args+[offset])
            items=[]
            for row in rows:
                note=json.loads(row[0]);item={key:note[key] for key in ('id','title','kind','scope','project','revision','muted','status','importance','updated_at','expires_at')};item.update(excerpt=note['body'][:130],source_count=len(note['sources']));items.append(item)
            return {'items':items,'total':total,'next_offset':offset+len(items) if offset+len(items)<total else None,'warnings':self.store.warnings[:5]}

    def state(self):
        with self.store.locked(), contextlib.closing(self.store._open_index()) as con:
            self.store._sync(con)
            counts=dict(con.execute("SELECT scope,count(*) FROM notes GROUP BY scope"));counts['all']=sum(counts.values());counts['muted']=con.execute('SELECT count(*) FROM notes WHERE muted=1').fetchone()[0];counts['provisional']=con.execute("SELECT count(*) FROM notes WHERE status='provisional'").fetchone()[0]
            projects=[{'path':p,'name':Path(p).name,'count':n}for p,n in con.execute("SELECT project,count(*) FROM notes WHERE scope='project' GROUP BY project ORDER BY project")]
            fingerprint=hashlib.sha256(repr(list(con.execute('SELECT id,mtime,size FROM notes ORDER BY id'))).encode()).hexdigest()
        configured=False
        try:
            home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
            server=tomllib.loads((home/'config.toml').read_text(encoding='utf-8'))['mcp_servers']['codex_memory']
            configured=server.get('enabled',True) and str(self.store.root/'memory.py') in server.get('args',[])
        except (OSError,KeyError,ValueError):pass
        return {'counts':counts,'projects':projects,'version':VERSION,'root':str(self.store.root),'data_dir':str(self.store.data),'codex_configured':bool(configured),'fingerprint':fingerprint,'warnings':self.store.warnings[:5],'preferences':self.preferences()}

    def note(self, ident):
        with self.store.locked():return self.store._load(ident)

    def history(self, ident):
        note=self.note(ident);folder=self.store._path('history',note['id']);items=[]
        if folder.exists():
            with self.store.locked():
                for path in sorted(folder.glob('*.md'),reverse=True)[:20]:
                    try:
                        front,body=self.store._safe(path).read_text(encoding='utf-8-sig').replace('\r\n','\n')[4:].split('\n---\n',1);meta=json.loads(front);items.append({'revision':meta['revision'],'updated_at':meta['updated_at'],'title':meta['title'],'body':body.strip()})
                    except (ValueError,KeyError):continue
        return {'items':items}

    def mutate(self, action, data):
        if action=='save':
            allowed={'title','body','kind','importance','scope','project'}
            if set(data)-allowed:raise MemoryFault('invalid','包含未知字段')
            body=data.get('body','');project=data.get('project','');note={k:v for k,v in data.items() if k!='project'};note.update(key=data.get('title'),evidence={'kind':'user','origin':'记忆面板：手动添加','text':body[:1000]})
            return self.store.dispatch('save',note,project)
        note=self.note(data.get('id'));allowed={'id','expected_revision','title','body','kind','importance'} if action=='revise'else {'id','expected_revision'}
        if set(data)-allowed:raise MemoryFault('invalid','包含未知字段')
        if action=='revise':
            data={**data,'evidence':{'kind':'user','origin':'记忆面板：手动更正','text':data.get('body',note['body'])[:1000]}}
        if action not in ('revise','mute','unmute','forget'):raise MemoryFault('invalid','未知操作')
        return self.store.dispatch(action,data,note['project'])


def create_server(root, assets=None, port=0):
    admin=MemoryAdmin(root);asset_root=Path(assets or Path(__file__).parent)
    static={'/':('ui/index.html','text/html; charset=utf-8'),'/app.js':('ui/app.js','application/javascript; charset=utf-8'),'/style.css':('ui/style.css','text/css; charset=utf-8'),'/icon.svg':('assets/icon.svg','image/svg+xml')}
    token=secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,value,json_body=True):
            raw=json.dumps(value,ensure_ascii=False).encode() if json_body else value
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8' if json_body else self.mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(raw)
        def check(self,write=False):
            host=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=host:return False
            if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return False
            if write and (self.headers.get('Origin')!='http://'+host or self.headers.get('Content-Type','').split(';')[0]!='application/json'):return False
            self.server.last_active=time.monotonic();return True
        def do_GET(self):
            path=urlsplit(self.path).path
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':return self.respond(403,{'message':'仅允许本机访问'})
            if path in static:
                filename,self.mime=static[path];return self.respond(200,(asset_root/filename).read_bytes(),False)
            if path=='/manifest.webmanifest':
                return self.respond(200,{'name':'Codex Memory','short_name':'Memory','id':'/','start_url':'/','scope':'/','display':'standalone','display_override':['window-controls-overlay','standalone'],'background_color':'#f4f3f7','theme_color':'#f4f3f7','icons':[{'src':'/icon.svg','sizes':'any','type':'image/svg+xml'}]})
            if not self.check():return self.respond(403,{'message':'请从“打开记忆面板”重新打开窗口'})
            try:
                if path=='/api/ping':result={'app':'codex-memory','version':VERSION,'root':str(admin.store.root)}
                elif path=='/api/state':result=admin.state()
                elif path=='/api/preferences':result=admin.preferences()
                elif path=='/api/window-state':result={**self.server.caption.status(),'error':self.server.window_error}
                elif path=='/api/notes':
                    q=parse_qs(urlsplit(self.path).query);result=admin.catalog(q.get('q',[''])[0],q.get('view',['all'])[0],q.get('project',[''])[0],int(q.get('offset',['0'])[0]),q.get('sort',['updated'])[0])
                elif path.startswith('/api/notes/'):
                    parts=path.removeprefix('/api/notes/').split('/');ident=parts[0]
                    if len(parts)==1:result=admin.note(ident)
                    elif len(parts)==2 and parts[1]=='sources':
                        note=admin.note(ident);result=admin.store.sources(ident,note['project'])
                    elif len(parts)==2 and parts[1]=='history':result=admin.history(ident)
                    else:return self.respond(404,{'message':'没有此页面'})
                else:return self.respond(404,{'message':'没有此页面'})
                self.respond(200,result)
            except (MemoryFault,ValueError) as error:self.respond(404 if getattr(error,'code','')=='not_found' else 400,{'message':str(error),'code':getattr(error,'code','invalid')})
            except Exception:self.respond(500,{'message':'暂时无法读取记忆，请稍后重试'})
        def do_POST(self):
            if not self.check(True):return self.respond(403,{'message':'请求来源无效，请重新打开面板'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:return self.respond(413,{'message':'内容过长'})
                data=json.loads(self.rfile.read(length));path=urlsplit(self.path).path
                if not isinstance(data,dict):raise MemoryFault('invalid','请求内容无效')
                if path=='/api/action':
                    if set(data)!={'action','data'} or not isinstance(data['data'],dict):raise MemoryFault('invalid','请求内容无效')
                    result=admin.mutate(data['action'],data['data'])
                elif path=='/api/preferences':
                    result=admin.preferences(data)
                elif path=='/api/window-state':
                    if set(data)!={'overlay'} or type(data['overlay']) is not bool:raise MemoryFault('invalid','窗口状态无效')
                    self.server.caption.overlay=data['overlay'];result=self.server.caption.status()
                elif path=='/api/open-window':
                    if data:raise MemoryFault('invalid','打开窗口不接受额外参数')
                    self.server.launch_window();result={'opened':True}
                elif path=='/api/open-folder':
                    if data.get('where') not in ('data','root') or set(data)!={'where'}:raise MemoryFault('invalid','目录无效')
                    os.startfile(admin.store.data if data['where']=='data' else admin.store.root);result={'opened':True}
                else:return self.respond(404,{'message':'没有此操作'})
                self.respond(200,result)
            except MemoryFault as error:self.respond(409 if error.code=='conflict' else 400,{'message':str(error),'code':error.code})
            except (ValueError,KeyError,TypeError):self.respond(400,{'message':'请求内容无效'})
            except Exception:self.respond(500,{'message':'操作未完成，请稍后重试；现有内容已保留'})

    try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    except OSError:
        if not port:raise
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True;server.token=token;server.admin=admin;server.last_active=time.monotonic();server.timeout=1
    profile=admin.store._safe(admin.store.data/'ui-edge')
    server.caption=WindowCaption(profile);server.window_error=''
    def launch_window():
        def launch():
            try:
                server.window_error=''
                server.caption.open(f'http://127.0.0.1:{server.server_port}/#'+server.token)
            except Exception as error:
                server.window_error=str(error)
                if os.name=='nt':
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None,str(error),'无法打开记忆面板',0x10)
        # Installation fetches the local manifest; the HTTP loop must already be serving.
        threading.Thread(target=launch,daemon=True,name='memory-app-launch').start()
    server.launch_window=launch_window
    return server


def run(root):
    store=MemoryStore(root);state_file=store._safe(store.data/'gui-state.json')
    port_file=store._safe(store.data/'gui-port.json')
    with store.locked():
        if state_file.exists():
            try:
                state=json.loads(state_file.read_text(encoding='utf-8'));base='http://127.0.0.1:'+str(int(state['port']))
                with urlopen(Request(base+'/api/ping',headers={'Authorization':'Bearer '+state['token']}),timeout=2) as response:ping=json.load(response)
                if ping.get('app')=='codex-memory' and ping.get('root')==str(store.root) and ping.get('version')==VERSION:
                    request=Request(base+'/api/open-window',data=b'{}',headers={'Authorization':'Bearer '+state['token'],'Origin':base,'Content-Type':'application/json'})
                    with urlopen(request,timeout=5):pass
                    return
            except (OSError,ValueError,KeyError):pass
        port=0
        try:
            saved=json.loads(port_file.read_text(encoding='utf-8'))
            if type(saved) is int and 1024<=saved<=65535:port=saved
        except (OSError,ValueError):pass
        server=create_server(root,port=port);atomic(port_file,json.dumps(server.server_port))
        state={'port':server.server_port,'token':server.token,'pid':os.getpid()};atomic(state_file,json.dumps(state))
    server.launch_window()
    try:
        while time.monotonic()-server.last_active<300:server.handle_request()
    finally:
        server.caption.close()
        server.server_close()
        with store.locked():
            if state_file.exists() and json.loads(state_file.read_text(encoding='utf-8')).get('token')==server.token:state_file.unlink()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('D:/codex-memory'));args=parser.parse_args()
    try:run(args.root)
    except Exception as error:
        if sys.stdout:raise
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'请重试；仍无法打开时，将此提示告诉Codex。\n'+type(error).__name__,'无法打开记忆面板',0x10)


if __name__=='__main__':main()
