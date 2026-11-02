import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gui import create_server


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-memory-gui-')
        self.addCleanup(self.temp.cleanup)
        self.server = create_server(self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.headers = {'Authorization':'Bearer '+self.server.token, 'Origin':self.base}

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, headers=None):
        values = {**self.headers, **(headers or {})}
        raw = None if data is None else json.dumps(data).encode()
        if raw is not None:
            values.setdefault('Content-Type','application/json')
        try:
            response = urlopen(Request(self.base+path, data=raw, headers=values), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, json.loads(body) if 'application/json' in response.headers.get('Content-Type','') else body

    def action(self, action, data):
        return self.request('/api/action', {'action':action, 'data':data})

    def save(self, title='测试偏好', project=''):
        code, result = self.action('save', {'title':title, 'body':'以后使用浅色主题', 'kind':'preference', 'scope':'project' if project else 'global', 'project':project, 'importance':2})
        self.assertEqual(code,200,result)
        return result

    def test_loopback_auth_origin_and_static_boundaries(self):
        self.save()
        for headers in ({'Authorization':''}, {'Authorization':'Bearer wrong'}, {'Host':'attacker.example'}):
            code, result = self.request('/api/notes', headers=headers)
            self.assertEqual(code,403)
            self.assertNotIn('测试偏好',str(result))
        for origin in ('https://attacker.example','null',''):
            code, _ = self.request('/api/action', {'action':'save','data':{}}, {'Origin':origin})
            self.assertEqual(code,403)
        self.assertEqual(self.request('/api/action', {}, {'Content-Type':'text/plain'})[0],403)
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/app.js')[0],200)
        self.assertEqual(self.request('/style.css')[0],200)
        self.assertEqual(self.request('/../store.py')[0],404)
        self.assertEqual(self.request('/api/notes/../../store.py')[0],404)

    def test_edit_evidence_conflict_mute_and_forget(self):
        saved = self.save()
        ident = saved['id']
        _, note = self.request('/api/notes/'+ident)
        self.assertEqual(note['body'],'以后使用浅色主题')
        status, updated = self.action('revise', {'id':ident,'expected_revision':1,'title':'更新偏好','body':'现在跟随系统主题','importance':3})
        self.assertEqual(status,200)
        self.assertEqual(updated['revision'],2)
        self.assertEqual(self.action('revise', {'id':ident,'expected_revision':1,'body':'不能覆盖新版本'})[0],409)
        _, history = self.request('/api/notes/'+ident+'/history')
        self.assertEqual(history['items'][0]['body'],'以后使用浅色主题')
        _, sources = self.request('/api/notes/'+ident+'/sources')
        self.assertEqual(len(sources['sources']),2)
        self.assertEqual(self.action('mute', {'id':ident,'expected_revision':2})[0],200)
        self.assertEqual(self.server.admin.store.search('偏好','')['items'],[])
        self.assertEqual(self.request('/api/notes?view=muted')[1]['total'],1)
        self.assertEqual(self.action('unmute', {'id':ident,'expected_revision':3})[0],200)
        self.assertTrue(self.server.admin.store.search('偏好','')['items'])
        self.assertEqual(self.action('forget', {'id':ident,'expected_revision':4})[0],200)
        self.assertEqual(self.request('/api/notes/'+ident)[0],404)
        self.assertEqual(self.request('/api/notes')[1]['total'],0)
        self.assertEqual(self.save_forgotten_again()[0],400)
        self.assertEqual(list((Path(self.temp.name)/'data/sources').glob('s-*.json')),[])

    def save_forgotten_again(self):
        return self.action('save', {'title':'测试偏好','body':'不应从旧内容恢复','scope':'global'})

    def test_human_overview_does_not_change_mcp_project_scope(self):
        first = str(Path(self.temp.name)/'project-a')
        second = str(Path(self.temp.name)/'project-b')
        self.save('全局偏好')
        a = self.save('A项目偏好',first)
        b = self.save('B项目偏好',second)
        _, result = self.request('/api/notes')
        self.assertEqual(result['total'],3)
        state = self.request('/api/state')[1]
        self.assertEqual(state['counts']['project'],2)
        self.assertEqual(len(state['fingerprint']),64)
        self.assertEqual(state['fingerprint'],self.request('/api/state')[1]['fingerprint'])
        recalled = self.server.admin.store.dispatch('list',project=first)['items']
        self.assertIn(a['id'],{item['id'] for item in recalled})
        self.assertNotIn(b['id'],{item['id'] for item in recalled})
        self.assertEqual(self.request('/api/notes?view=global')[1]['total'],1)
        self.assertEqual(self.request('/api/notes?q='+quote('A项目'))[0],200)

    def test_invalid_requests_preserve_existing_content(self):
        saved = self.save()
        for data in ({}, [], {'action':'save','data':[]}, {'action':'save','data':{'title':'wrong','body':12}}):
            self.assertEqual(self.request('/api/action',data)[0],400)
        self.assertEqual(self.request('/api/notes?offset=invalid')[0],400)
        self.assertEqual(self.action('revise',{'id':saved['id'],'expected_revision':1,'project':'/other'})[0],400)
        self.assertEqual(self.request('/api/open-folder',{'where':'../../other'})[0],400)
        self.assertEqual(self.request('/api/notes')[1]['total'],1)

    def test_preferences_persist_across_server_restarts_without_touching_notes(self):
        self.save()
        original={p.name:p.read_bytes() for p in (Path(self.temp.name)/'data/notes').glob('*.md')}
        defaults=self.request('/api/preferences')[1]
        self.assertTrue(defaults['motion'])
        status,updated=self.request('/api/preferences',{'accent':'#00AACC','motion':False,'theme':'dark'})
        self.assertEqual(status,200)
        self.assertEqual(updated,{'accent':'#00aacc','motion':False,'theme':'dark','zoom':100,'sort':'updated'})
        from gui import MemoryAdmin
        self.assertEqual(MemoryAdmin(self.temp.name).preferences(),updated)
        self.assertEqual(original,{p.name:p.read_bytes() for p in (Path(self.temp.name)/'data/notes').glob('*.md')})
        self.assertEqual(self.request('/manifest.webmanifest')[0],200)

    def test_invalid_or_cross_origin_preferences_cannot_override_settings(self):
        self.request('/api/preferences',{'accent':'#112233'})
        for patch in ({'accent':'red; url(https://example.com)'},{'motion':'false'},{'theme':'unknown'},{'root':'other'},{'zoom':10},{'zoom':True},{'zoom':151},{'sort':'drop table'}):
            self.assertEqual(self.request('/api/preferences',patch)[0],400)
        self.assertEqual(self.request('/api/preferences',{'motion':False},{'Origin':'https://attacker.example'})[0],403)
        self.assertEqual(self.request('/api/preferences')[1]['accent'],'#112233')
        self.assertTrue(self.request('/api/preferences')[1]['motion'])

    def test_sort_orders_and_invalid_window_requests(self):
        a=self.action('save',{'title':'Beta','body':'测试','scope':'global','importance':1})[1]
        b=self.action('save',{'title':'Alpha','body':'测试','scope':'global','importance':3})[1]
        self.action('revise',{'id':a['id'],'expected_revision':1,'body':'更新后的测试'})
        for order,first in (('updated',a['id']),('created',b['id']),('importance',b['id']),('title',b['id'])):
            status,result=self.request('/api/notes?sort='+order)
            self.assertEqual(status,200)
            self.assertEqual(result['items'][0]['id'],first)
        self.assertEqual(self.request('/api/notes?sort=unknown')[0],400)
        self.assertEqual(self.request('/api/window-state',{'overlay':True,'hwnd':1})[0],400)
        self.assertEqual(self.request('/api/window-state',{'overlay':'true'})[0],400)
        self.assertEqual(self.request('/api/window-state',{'overlay':True},{'Origin':'https://attacker.example'})[0],403)
        self.assertTrue(self.request('/api/window-state',{'overlay':True})[1]['overlay'])
        self.assertEqual(self.request('/api/window-action',{'action':'close','hwnd':1})[0],404)


if __name__=='__main__':
    unittest.main(verbosity=2)
