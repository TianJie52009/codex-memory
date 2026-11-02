from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from store import MemoryStore, MemoryFault, project_key, dump


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-memory-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / 'runtime')
        self.a = project_key(str(self.root / 'project-a'))
        self.b = project_key(str(self.root / 'project-b'))

    def evidence(self, text='用户明确说喜欢紫色星空', kind='user', origin='test:user:1'):
        return {'kind': kind, 'origin': origin, 'text': text}

    def save(self, key='主题', project=None, **extra):
        payload = {'key': key, 'title': key, 'body': '喜欢克制的紫色星空', 'kind': 'preference', 'evidence': self.evidence(), **extra}
        return self.store.dispatch('save', payload, self.a if project is None else project)

    def read(self, result, project=None):
        return self.store.dispatch('read', {'id': result['id']}, self.a if project is None else project)['note']

    def test_global_and_project_ranges(self):
        common = self.save('共同偏好', scope='global')
        private = self.save('项目A方案')
        self.assertEqual({x['id'] for x in self.store.dispatch('search', {'query': '紫色'}, self.a)['items']}, {common['id'], private['id']})
        self.assertEqual([x['id'] for x in self.store.dispatch('search', {'query': '紫色'}, self.b)['items']], [common['id']])
        self.assertEqual([x['id'] for x in self.store.dispatch('search', {'query': '紫色'})['items']], [common['id']])
        with self.assertRaises(MemoryFault):
            self.read(private, self.b)
        with self.assertRaises(MemoryFault):
            self.store.dispatch('read', {'id': '../notes'}, self.a)

    def test_chinese_and_alias_search(self):
        a = self.save('界面规范', aliases=['macOS', '苹果设计'])
        self.assertEqual(self.store.dispatch('search', {'query': '紫色星空'}, self.a)['items'][0]['id'], a['id'])
        self.assertEqual(self.store.dispatch('search', {'query': 'macOS'}, self.a)['items'][0]['id'], a['id'])
        self.assertEqual(self.store.dispatch('search', {'query': '\" OR 1=1 --'}, self.b)['items'], [])

    def test_evidence_and_inference_boundaries(self):
        with self.assertRaises(MemoryFault):
            self.save(evidence={})
        with self.assertRaises(MemoryFault):
            self.save(evidence=self.evidence('api_key=' + 'z' * 40))
        self.assertEqual(self.store.status()['notes'], 0)
        self.assertEqual(self.store.status()['sources'], 0)
        a = self.save(evidence=self.evidence('可能喜欢紫色', 'inference'))
        self.assertEqual(self.read(a)['status'], 'provisional')
        self.assertEqual(self.store.dispatch('context', project=self.a)['items'], [])

    def test_evidence_is_a_bounded_excerpt_not_a_chat_archive(self):
        with self.assertRaises(MemoryFault):
            self.save(evidence=self.evidence('对话片段' * 251))
        self.assertEqual(self.store.status()['sources'], 0)
        self.assertEqual(self.store.status()['notes'], 0)

    def test_idempotence_revision_and_history(self):
        a = self.save()
        self.assertTrue(self.save()['unchanged'])
        self.assertEqual(self.store.status()['sources'], 1)
        with self.assertRaises(MemoryFault):
            self.save(body='现在喜欢蓝色')
        b = self.store.dispatch('revise', {'id': a['id'], 'expected_revision': 1, 'body': '现在喜欢蓝色', 'evidence': self.evidence('现在改成蓝色', origin='test:user:2')}, self.a)
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(self.read(b)['revision'], 2)
        self.assertEqual(len(list((self.store.data/'history'/a['id']).glob('*.md'))), 1)
        self.assertEqual(len(self.store.dispatch('sources', {'id': a['id']}, self.a)['sources']), 2)

    def test_concurrent_correction_rejects_stale_writer(self):
        a = self.save()
        def change(value):
            try:
                return MemoryStore(self.store.root).dispatch('revise', {'id': a['id'], 'expected_revision': 1, 'body': value, 'evidence': self.evidence(value)}, self.a)
            except MemoryFault as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ['改成蓝色', '改成绿色']))
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)
        self.assertIn('conflict', results)
        self.assertEqual(self.read(a)['revision'], 2)

    def test_forget_removes_history_sources_and_blocks_reimport(self):
        a = self.save(key='forgotten-secret-fact')
        b = self.store.dispatch('revise', {'id': a['id'], 'expected_revision': 1, 'body': '更正后的文字', 'evidence': self.evidence('用户更正')}, self.a)
        self.store.dispatch('search', {'query': 'forgotten'}, self.a)
        result = self.store.dispatch('forget', {'id': b['id'], 'expected_revision': 2}, self.a)
        self.assertEqual(result['deleted_history'], 1)
        self.assertEqual(result['deleted_orphan_sources'], 2)
        self.assertEqual(self.store.dispatch('list', project=self.a)['items'], [])
        with self.assertRaises(MemoryFault):
            self.save(key='forgotten-secret-fact')
        restored = self.save(key='forgotten-secret-fact', body='重新明确需要记忆的新内容', restore_forgotten=True)
        self.assertEqual(restored['id'], a['id'])
        self.assertEqual(self.read(restored)['revision'], 1)

    def test_mute_related_and_expiry(self):
        public = self.save('全局', scope='global')
        a = self.save('相关项目', related=[public['id']])
        self.assertEqual(self.store.dispatch('related', {'id': a['id']}, self.a)['items'][0]['id'], public['id'])
        with self.assertRaises(MemoryFault):
            self.save('不能全局引用项目', scope='global', related=[a['id']])
        self.store.dispatch('mute', {'id': a['id'], 'expected_revision': 1}, self.a)
        self.assertNotIn(a['id'], [x['id'] for x in self.store.dispatch('search', {'query': '紫色'}, self.a)['items']])
        self.assertTrue(self.read(a)['muted'])
        self.store.dispatch('unmute', {'id': a['id'], 'expected_revision': 2}, self.a)
        expired = self.save('旧阶段', expires_at='2020-01-01T00:00:00+08:00')
        self.assertTrue(self.store.dispatch('read', {'id': expired['id']}, self.a)['expired'])
        self.assertNotIn(expired['id'], [x['id'] for x in self.store.dispatch('search', {'query': '旧阶段'}, self.a)['items']])

    def test_search_and_context_budgets(self):
        for i in range(8):
            self.save(f'同主题{i}', body='共同偏好' * 1000)
        full = self.store.dispatch('search', {'query': '共同偏好'}, self.a)
        small = self.store.dispatch('context', project=self.a)
        self.assertLessEqual(len(full['items']), 5)
        self.assertLessEqual(len(dump(full['items'])), 1500)
        self.assertLessEqual(len(small['items']), 3)
        self.assertLessEqual(len(dump(small['items'])), 500)

    def test_index_recovery_and_manual_markdown_update(self):
        a = self.save()
        self.store.dispatch('search', {'query': '紫色'}, self.a)
        self.store.index.write_bytes(b'broken sqlite')
        self.assertEqual(self.store.dispatch('search', {'query': '紫色'}, self.a)['items'][0]['id'], a['id'])
        path = self.store.data/'notes'/(a['id']+'.md')
        raw = path.read_text(encoding='utf-8').replace('喜欢克制的紫色星空', '现在采用深蓝色星空')
        path.write_text(raw, encoding='utf-8')
        self.assertIn('深蓝色', self.store.dispatch('search', {'query': '深蓝色'}, self.a)['items'][0]['excerpt'])

    def test_missing_evidence_damaged_note_is_retained_but_not_recalled(self):
        a = self.save()
        self.store.dispatch('search', {'query': '紫色'}, self.a)
        note = self.read(a)
        (self.store.data/'sources'/(note['sources'][0]+'.json')).unlink()
        result = self.store.dispatch('search', {'query': '紫色'}, self.a)
        self.assertEqual(result['items'], [])
        self.assertEqual(result['warnings'][0]['id'], a['id'])
        self.assertTrue((self.store.data/'notes'/(a['id']+'.md')).exists())

    def test_fresh_environment_restore_and_continued_growth(self):
        a = self.save()
        self.store.status()
        fresh = self.root/'restored'
        shutil.copytree(self.store.data, fresh/'data')
        (fresh/'data'/'index.sqlite3').unlink()
        restored = MemoryStore(fresh)
        self.assertEqual(restored.dispatch('search', {'query': '紫色'}, self.a)['items'][0]['id'], a['id'])
        changed = restored.dispatch('revise', {'id': a['id'], 'expected_revision': 1, 'body': '换机后可以继续更正记忆', 'evidence': self.evidence('换机更正')}, self.a)
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(self.read(a)['revision'], 1)

    def test_invalid_input_has_no_write(self):
        for payload in ({'query': 123}, {'query': 'a', 'include_muted': 'false'}):
            with self.assertRaises(MemoryFault):
                self.store.dispatch('search', payload, self.a)
        with self.assertRaises(MemoryFault):
            self.store.dispatch('save', {'key': 'x'}, 'relative/project')
        self.assertEqual(self.store.status()['notes'], 0)
        self.assertTrue(Path(project_key(str(self.root.anchor))).is_absolute())


if __name__ == '__main__':
    unittest.main(verbosity=2)
