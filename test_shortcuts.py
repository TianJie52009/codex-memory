import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from windows_taskbar import _properties,app_id
from shortcuts import create_shortcuts


@unittest.skipUnless(os.name=='nt','Windows shortcut properties')
class ShortcutTests(unittest.TestCase):
    def test_migrate_only_our_legacy_edge_startup_and_keep_a_backup(self):
        original_run=subprocess.run
        with tempfile.TemporaryDirectory(prefix='memory-startup-test-') as folder:
            base=Path(folder);root=base/'app';desktop=base/'Desktop';startup=base/'Startup'
            for path in (root,desktop,startup):path.mkdir()
            legacy=startup/'Codex Memory.lnk';edge=base/'msedge_proxy.exe';edge.write_bytes(b'fixture, never executed')
            def legacy_link(profile):
                script=r'''$p=[Console]::In.ReadToEnd() | ConvertFrom-Json; $s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($p.path); $l.TargetPath=$p.edge; $l.Arguments=$p.args; $l.Save()'''
                original_run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],input=json.dumps({'path':str(legacy),'edge':str(edge),'args':'--user-data-dir="'+str(profile)+'" --app-id='+'a'*32}),text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            def isolated_run(args,**kwargs):
                args=list(args)
                args[-1]=args[-1].replace("[Environment]::GetFolderPath('Desktop')",'$p.fixtureDesktop').replace("[Environment]::GetFolderPath('Startup')",'$p.fixtureStartup')
                payload=json.loads(kwargs['input']);payload.update(fixtureDesktop=str(desktop),fixtureStartup=str(startup))
                return original_run(args,**{**kwargs,'input':json.dumps(payload)})
            legacy_link(root/'data/ui-edge');old=legacy.read_bytes()
            with patch('shortcuts.subprocess.run',side_effect=isolated_run):links=create_shortcuts(root,True)
            self.assertEqual(len(links),3)
            self.assertEqual(_properties(path=legacy)[5],app_id(root))
            backups=list((root/'install-backups').glob('shortcut-*/*.lnk'))
            self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_bytes(),old)
            legacy_link(base/'some-other-app/data/ui-edge');foreign=legacy.read_bytes()
            with patch('shortcuts.subprocess.run',side_effect=isolated_run):
                with self.assertRaises(subprocess.CalledProcessError):create_shortcuts(root,True)
            self.assertEqual(legacy.read_bytes(),foreign)

    def test_taskbar_identity_persists_without_changing_launcher(self):
        with tempfile.TemporaryDirectory(prefix='memory-link-test-') as root:
            path=Path(root)/'only-this-test.lnk'
            script=r'''$p=[Console]::In.ReadToEnd() | ConvertFrom-Json; $s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($p.path); $l.TargetPath=$p.python; $l.Arguments='-V'; $l.Save()'''
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],input=json.dumps({'path':str(path),'python':sys.executable}),text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            ident=app_id(root)
            _properties(path=path,values={5:ident})
            self.assertEqual(_properties(path=path)[5],ident)
            read=r'''$p=[Console]::In.ReadToEnd(); $s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($p); ConvertTo-Json @{target=$l.TargetPath;args=$l.Arguments} -Compress'''
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',read],input=str(path),text=True,capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            link=json.loads(result.stdout)
            self.assertEqual(Path(link['target']),Path(sys.executable))
            self.assertEqual(link['args'],'-V')


if __name__=='__main__':unittest.main(verbosity=2)
