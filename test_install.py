import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-memory-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'src'
        self.source.mkdir()
        real = Path(__file__).parent
        for name in install.FILES:
            (self.source/name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(real/name, self.source/name)
        self.target, self.home = self.root/'runtime', self.root/'codex-home'
        self.home.mkdir()
        self.config = self.home/'config.toml'
        self.agents = self.home/'AGENTS.md'
        self.config.write_text('model="keep-original"\n[features]\nmemories=true\n[mcp_servers.other]\ncommand="existing-server"\n',encoding='utf-8')
        self.agents.write_text('原来的Apple设计约定必须保留。\n',encoding='utf-8')

    def test_install_and_reinstall_preserve_user_config_and_data(self):
        before = tomllib.loads(self.config.read_text(encoding='utf-8'))
        first = install.install(self.source,self.target,self.home)
        self.assertTrue(first['data_preserved'])
        after = tomllib.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(after['features'],before['features'])
        self.assertEqual(after['model'],before['model'])
        self.assertEqual(after['mcp_servers']['other'],before['mcp_servers']['other'])
        self.assertTrue(self.agents.read_text(encoding='utf-8').startswith('原来的Apple设计约定必须保留。'))
        self.assertEqual(self.agents.read_text(encoding='utf-8').count(install.START),1)
        data = self.target/'data'
        data.mkdir()
        sentinel = data/'user-memory.md'
        sentinel.write_text('用户真实记忆要保留',encoding='utf-8')
        second = install.install(self.source,self.target,self.home)
        self.assertEqual(second['changed_files'],0)
        self.assertEqual(sentinel.read_text(encoding='utf-8'),'用户真实记忆要保留')
        (self.source/'store.py').write_bytes((self.source/'store.py').read_bytes()+b'\n# updated source\n')
        third = install.install(self.source,self.target,self.home)
        self.assertEqual(third['changed_files'],1)
        self.assertEqual(sentinel.read_text(encoding='utf-8'),'用户真实记忆要保留')
        self.assertTrue(list((self.target/'install-backups').glob('*/000-store.py')))

    def test_refuse_unowned_file_and_existing_server(self):
        self.target.mkdir()
        (self.target/'README.md').write_text('已有的其他项目',encoding='utf-8')
        with self.assertRaises(ValueError):
            install.install(self.source,self.target,self.home)
        self.assertEqual((self.target/'README.md').read_text(encoding='utf-8'),'已有的其他项目')
        (self.target/'README.md').unlink()
        self.config.write_text('[mcp_servers.codex_memory]\ncommand="something-else"\nargs=[]\n',encoding='utf-8')
        with self.assertRaises(ValueError):
            install.install(self.source,self.target,self.home)
        self.assertFalse((self.target/'store.py').exists())

    def test_override_or_broken_marker_does_not_silently_install(self):
        (self.home/'AGENTS.override.md').write_text('临时全局约定',encoding='utf-8')
        with self.assertRaises(ValueError):
            install.install(self.source,self.target,self.home)
        (self.home/'AGENTS.override.md').unlink()
        self.agents.write_text(install.START+'缺少结束标记',encoding='utf-8')
        with self.assertRaises(ValueError):
            install.install(self.source,self.target,self.home)
        self.assertFalse((self.target/'memory.py').exists())

    def test_failed_config_write_restores_prior_files(self):
        old_config,old_agents = self.config.read_bytes(),self.agents.read_bytes()
        original = install.atomic
        def fail_once(path,content):
            if path == self.config:
                raise OSError('模拟写入失败')
            return original(path,content)
        with patch.object(install,'atomic',side_effect=fail_once):
            with self.assertRaises(OSError):
                install.install(self.source,self.target,self.home)
        self.assertEqual(self.config.read_bytes(),old_config)
        self.assertEqual(self.agents.read_bytes(),old_agents)
        self.assertFalse((self.target/'store.py').exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
