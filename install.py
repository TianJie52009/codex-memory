"""Install owned code + a small Codex MCP/instructions block; preserve runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import tomllib

from store import atomic, VERSION

FILES = ('store.py', 'memory.py', 'gui.py', 'native_caption.py', 'shortcuts.py', 'install.py', 'requirements.txt', 'README.md',
         'USER_GUIDE.md', 'GLOBAL_AGENTS.md', 'AGENTS.md', 'CHANGELOG.md', 'test_store.py', 'test_mcp.py',
         'test_install.py', 'test_gui.py', 'test_native_window.py', '.gitignore', 'ui/index.html', 'ui/style.css', 'ui/app.js',
         'assets/icon.svg', 'assets/icon.ico', 'assets/badges.svg', 'assets/screenshot-light.png', 'assets/screenshot-dark.png', 'assets/screenshot-settings.png')
START, END = '<!-- codex-memory:start -->', '<!-- codex-memory:end -->'


def instruction_text(base, block):
    if (START in base) != (END in base):
        raise ValueError('AGENTS中的记忆标记不完整，请先核对')
    if START in base:
        return re.sub(re.escape(START) + r'.*?' + re.escape(END), lambda _: block, base, flags=re.S)
    return base + ('\n' if base.endswith('\n') else '\n\n') + block + '\n'


def install(source, target, codex_home):
    source = Path(source).resolve()
    raw_target = Path(target)
    if not raw_target.is_absolute() or raw_target.is_symlink():
        raise ValueError('目标必须是非链接的绝对目录')
    target = raw_target.resolve()
    if len(target.parts) < 2 or target == Path(target.anchor):
        raise ValueError('不能安装到磁盘根目录')
    target.mkdir(parents=True, exist_ok=True)
    codex_home = Path(codex_home).resolve()
    codex_home.mkdir(parents=True, exist_ok=True)
    config, agents = codex_home/'config.toml', codex_home/'AGENTS.md'
    state_path = target/'.install-state.json'
    if any(p.is_symlink() for p in (state_path, config, agents)):
        raise ValueError('配置、指令或安装清单不能是符号链接')
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    proposed = {}
    for name in FILES:
        incoming = (source/name).read_bytes()
        destination = target/name
        if destination.is_symlink() or not destination.resolve().is_relative_to(target) or any(p.is_symlink() for p in destination.parents if p != target and p.is_relative_to(target)):
            raise ValueError('安装文件路径不能跳出目标目录')
        if destination.exists() and destination.read_bytes() != incoming and name not in state.get('files', {}):
            raise ValueError(f'目标已有未归本安装器管理的{name}，未覆盖')
        proposed[destination] = incoming
    config_raw = config.read_bytes() if config.exists() else b''
    config_text = config_raw.decode('utf-8-sig')
    old_config = tomllib.loads(config_text)
    args = ['-X', 'utf8', '-u', str(target/'memory.py'), '--root', str(target), 'serve']
    server = {'command': sys.executable, 'args': args, 'enabled': True, 'startup_timeout_sec': 20, 'tool_timeout_sec': 30}
    existing = old_config.get('mcp_servers', {}).get('codex_memory')
    if existing is not None:
        if existing.get('command') != server['command'] or existing.get('args') != args:
            raise ValueError('已有其他codex_memory MCP配置，未覆盖；请核对其用途')
    else:
        block = '\n[mcp_servers.codex_memory]\n' + '\n'.join(f'{key} = {json.dumps(value, ensure_ascii=False)}' for key,value in server.items()) + '\n'
        updated = config_text + ('\n' if config_text and not config_text.endswith('\n') else '') + block
        parsed = tomllib.loads(updated)
        assert {key:value for key,value in parsed.items() if key != 'mcp_servers'} == {key:value for key,value in old_config.items() if key != 'mcp_servers'}
        for key,value in old_config.get('mcp_servers', {}).items():
            assert parsed['mcp_servers'][key] == value
        proposed[config] = updated.encode('utf-8')
    agents_text = agents.read_text(encoding='utf-8-sig') if agents.exists() else ''
    override = codex_home/'AGENTS.override.md'
    if override.exists() and override.read_text(encoding='utf-8-sig').strip():
        raise ValueError('存在全局AGENTS.override.md，需先明确指令入口，未写入可能无效的AGENTS.md')
    template = (source/'GLOBAL_AGENTS.md').read_text(encoding='utf-8-sig').replace('{{ROOT}}', str(target))
    block = START + '\n' + template.strip() + '\n' + END
    proposed[agents] = instruction_text(agents_text, block).encode('utf-8')
    changes = {p:data for p,data in proposed.items() if not p.exists() or p.read_bytes() != data}
    if not changes:
        return {'version': VERSION, 'root': str(target), 'changed_files': 0, 'data_preserved': True}
    backup = target/'install-backups'/str(time.time_ns())
    if backup.is_symlink() or not backup.resolve().is_relative_to(target):
        raise ValueError('备份路径必须在安装目录内')
    backup.mkdir(parents=True)
    previous = {}
    for index, path in enumerate(changes):
        previous[path] = path.read_bytes() if path.exists() else None
        if previous[path] is not None:
            atomic(backup/f'{index:03d}-{path.name}', previous[path])
    atomic(backup/'paths.json', json.dumps([{'path':str(p),'existed':raw is not None} for p,raw in previous.items()], ensure_ascii=False, indent=2))
    written = []
    try:
        for path,data in changes.items():
            path.parent.mkdir(parents=True,exist_ok=True)
            atomic(path, data)
            written.append(path)
        manifest = {'version':VERSION, 'target':str(target), 'files':{name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in FILES}}
        atomic(state_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    except BaseException:
        for path in reversed(written):
            if previous[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, previous[path])
        raise
    return {'version':VERSION,'root':str(target),'changed_files':len(changes),'backup':str(backup),'data_preserved':True,'restart_codex_to_load_mcp':True}


def main():
    parser = argparse.ArgumentParser(description='从开发目录部署源码并注册本地Codex记忆MCP；不覆盖data')
    parser.add_argument('--target',type=Path,default=Path('D:/codex-memory'))
    parser.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    args = parser.parse_args()
    result=install(Path(__file__).parent,args.target,args.codex_home)
    from shortcuts import create_shortcuts
    result['gui_shortcuts']=create_shortcuts(args.target)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    raise SystemExit(main())
