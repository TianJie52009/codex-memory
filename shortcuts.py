"""Create Windows shortcuts to pythonw; no terminal and no file-association setup."""
import json
import os
from pathlib import Path
import subprocess
import sys


def create_shortcuts(root,autostart=False):
    if os.name != 'nt':
        return []
    root=Path(root).resolve()
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():
        raise OSError('找不到pythonw.exe，无法创建无终端启动入口')
    payload={'root':str(root),'python':str(pythonw),
             'args':subprocess.list2cmdline(['-X','utf8',str(root/'gui.py'),'--root',str(root)]),
             'icon':str(root/'assets/icon.ico'),'name':'打开记忆面板.lnk','autostart':bool(autostart)}
    script=r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
$paths = @((Join-Path $p.root $p.name), (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex Memory.lnk'))
if ($p.autostart) { $paths += Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Memory.lnk' }
foreach ($path in $paths) {
    $launchArgs = $p.args
    if ([IO.Path]::GetDirectoryName($path) -eq [Environment]::GetFolderPath('Startup')) { $launchArgs += ' --background' }
    $link = $shell.CreateShortcut($path)
    if ((Test-Path -LiteralPath $path) -and ($link.TargetPath -ne $p.python -or $link.Arguments -ne $launchArgs)) {
        $profile = [regex]::Escape((Join-Path $p.root 'data\ui-edge'))
        $ownLegacyEdge = ([IO.Path]::GetFileName($link.TargetPath) -in @('msedge.exe','msedge_proxy.exe')) -and ($link.Arguments -match ('(?:^|\s)--user-data-dir="?' + $profile + '(?:"|(?=\s|$))')) -and ($link.Arguments -match '--app-id=[a-p]{32}(?:\s|$)')
        if (-not $ownLegacyEdge) { throw 'Shortcut name is already in use; existing file was preserved.' }
        $backup = Join-Path $p.root ('install-backups\shortcut-' + [DateTime]::UtcNow.Ticks)
        [IO.Directory]::CreateDirectory($backup) | Out-Null
        Copy-Item -LiteralPath $path -Destination (Join-Path $backup ([IO.Path]::GetFileName($path)))
    }
    $link.TargetPath = $p.python
    $link.Arguments = $launchArgs
    $link.WorkingDirectory = $p.root
    $link.IconLocation = $p.icon + ',0'
    $link.Description = 'Codex Memory - local memory manager'
    $link.Save()
}
ConvertTo-Json -InputObject @($paths) -Compress
'''
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                          input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW,check=True)
    paths=json.loads(result.stdout.strip().lstrip('\ufeff'))
    from windows_taskbar import _properties,app_id
    for path in paths:_properties(path=path,values={5:app_id(root)})
    return paths


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='只创建图形面板快捷方式，不改任何Agent客户端配置')
    parser.add_argument('--root',type=Path,default=Path(__file__).parent)
    parser.add_argument('--autostart',action='store_true',help='登录Windows后仅启动后台服务')
    args=parser.parse_args()
    print(json.dumps(create_shortcuts(args.root,args.autostart),ensure_ascii=False,indent=2))
