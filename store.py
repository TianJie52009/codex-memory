"""Local evidence-backed Markdown memories; SQLite is a disposable search index."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unicodedata
from datetime import datetime, timezone

VERSION = '1.5.0'
KINDS = {'fact', 'preference', 'decision', 'workflow', 'state', 'reference'}
ID = re.compile(r'm-[0-9a-f]{24}\Z')
SOURCE_ID = re.compile(r's-[0-9a-f]{24}\Z')
SECRET = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\bBearer\s+[A-Za-z0-9_.-]{20,}|(?:api[_-]?key|password|secret)\s*[:=]\s*[\"\']?[A-Za-z0-9_/-]{20,}', re.I)


class MemoryFault(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def text(value, name, maximum=6000, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum or '\x00' in value:
        raise MemoryFault('invalid', f'{name} 必须是{maximum}字以内的有效文字')
    if SECRET.search(value):
        raise MemoryFault('secret', '内容疑似包含密钥或密码，请先移除凭据')
    return value.strip()


def project_key(value=''):
    if not value:
        return ''
    value = text(value, 'project', 1000)
    p = Path(value)
    if not p.is_absolute():
        raise MemoryFault('project', 'project 必须是项目绝对路径')
    normalized = os.path.normcase(str(p.resolve()))
    return normalized if Path(normalized) == Path(Path(normalized).anchor) else normalized.rstrip('\\/')


def timestamp(value):
    if not value:
        return ''
    value = text(value, '时间', 64)
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except ValueError:
        raise MemoryFault('invalid', '时间必须是含时区的ISO 8601格式') from None


def tokens(value):
    result = set()
    for word in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', unicodedata.normalize('NFKC', value).casefold()):
        result.add(word)
        if re.fullmatch(r'[\u3400-\u9fff]+', word):
            result.update(word)
            result.update(word[i:i+2] for i in range(len(word)-1))
    return result


def atomic(path, content):
    fd, temp = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class MemoryStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.data = self.root / 'data'
        self.data.mkdir(exist_ok=True)
        self._safe(self.data)
        self.directories = {}
        for name in ('notes', 'sources', 'history', 'forgotten'):
            p = self.data / name
            p.mkdir(exist_ok=True)
            self._safe(p)
            self.directories[name] = p
        self._safe(self.data)
        self.index = self.data / 'index.sqlite3'
        self.warnings = []

    def _safe(self, path):
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise MemoryFault('path', '记忆路径必须位于当前记忆根目录内，不能使用链接跳出')
        return path

    def _path(self, section, name):
        return self._safe(self.directories[section] / name)

    @contextlib.contextmanager
    def locked(self):
        path = self._safe(self.data / '.write.lock')
        with open(path, 'a+b') as file:
            file.seek(0, 2)
            if file.tell() == 0:
                file.write(b'0')
                file.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    file.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise MemoryFault('busy', '记忆正在写入，请稍后重试') from None
                    time.sleep(.03)
            try:
                yield
            finally:
                file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file, fcntl.LOCK_UN)

    def _id(self, value):
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise MemoryFault('invalid', '无效的记忆ID')
        return value

    def _load(self, ident):
        ident = self._id(ident)
        if self._path('forgotten', ident + '.json').exists():
            raise MemoryFault('not_found', '当前范围中没有这条记忆')
        p = self._path('notes', ident + '.md')
        if not p.is_file():
            raise MemoryFault('not_found', '当前范围中没有这条记忆')
        try:
            raw = p.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
            if not raw.startswith('---\n'):
                raise ValueError()
            front, body = raw[4:].split('\n---\n', 1)
            note = json.loads(front)
            assert note['id'] == ident and note['revision'] >= 1
            assert note['scope'] in ('global', 'project') and note['kind'] in KINDS
            assert note['project'] == project_key(note['project'])
            assert (note['scope'] == 'global') == (note['project'] == '')
            assert ident == 'm-' + digest(dump([note['scope'], note['project'], note['key']]))
            assert note['sources'] and all(SOURCE_ID.fullmatch(s) for s in note['sources'])
            for sid in note['sources']:
                assert self._path('sources', sid + '.json').is_file()
            assert isinstance(note['muted'], bool) and note['status'] in ('active', 'provisional')
            assert 1 <= note['importance'] <= 3
            timestamp(note['updated_at'])
            timestamp(note.get('expires_at', ''))
            note['title'] = text(note['title'], 'title', 120)
            note['body'] = text(body, 'body', 6000)
            return note
        except (ValueError, AssertionError, KeyError, TypeError):
            raise MemoryFault('damaged', f'{ident}.md 元数据或来源无效；文件保留，请用历史副本修复') from None

    def _visible(self, note, project):
        return note['scope'] == 'global' or (bool(project) and note['project'] == project)

    def _get(self, ident, project):
        note = self._load(ident)
        if not self._visible(note, project):
            raise MemoryFault('not_found', '当前范围中没有这条记忆')
        return note

    def _source(self, evidence):
        if not isinstance(evidence, dict) or set(evidence) - {'kind', 'origin', 'text', 'observed_at'}:
            raise MemoryFault('evidence', 'evidence 需要 kind、origin、text，可选 observed_at')
        kind = evidence.get('kind')
        if kind not in ('user', 'verified', 'inference'):
            raise MemoryFault('evidence', '来源kind只能为user、verified或inference')
        origin = text(evidence.get('origin'), 'evidence.origin', 500)
        quote = text(evidence.get('text'), 'evidence.text', 1000)
        observed = timestamp(evidence.get('observed_at', ''))
        ident = 's-' + digest(dump([kind, origin, quote, observed]))
        path = self._path('sources', ident + '.json')
        if not path.exists():
            atomic(path, dump({'id': ident, 'kind': kind, 'origin': origin, 'text': quote, 'observed_at': observed or utc()}))
        return ident, kind

    def _write(self, note, prior=None):
        if prior:
            history = self._path('history', note['id'])
            history.mkdir(exist_ok=True)
            old = self._path('notes', note['id'] + '.md').read_bytes()
            atomic(self._safe(history / f"{prior['revision']:06d}.md"), old)
        meta = {key: value for key, value in note.items() if key != 'body'}
        atomic(self._path('notes', note['id'] + '.md'), '---\n' + json.dumps(meta, ensure_ascii=False, indent=2) + '\n---\n\n' + note['body'] + '\n')
        return {'id': note['id'], 'revision': note['revision'], 'status': note['status'], 'saved': True}

    def save(self, data, project=''):
        allowed = {'key', 'title', 'body', 'kind', 'scope', 'aliases', 'related', 'importance', 'expires_at', 'evidence', 'restore_forgotten'}
        if set(data) - allowed:
            raise MemoryFault('invalid', 'save 包含未知字段')
        if type(data.get('restore_forgotten', False)) is not bool:
            raise MemoryFault('invalid', 'restore_forgotten须为布尔值')
        key, title, body = (text(data.get(field), field, limit) for field, limit in [('key', 120), ('title', 120), ('body', 6000)])
        scope, kind = data.get('scope', 'project'), data.get('kind', 'fact')
        if scope not in ('global', 'project') or kind not in KINDS or (scope == 'project' and not project):
            raise MemoryFault('scope', '项目记忆需要project绝对路径；全局记忆请显式scope=global')
        partition = project if scope == 'project' else ''
        ident = 'm-' + digest(dump([scope, partition, key]))
        fields = self._fields(data, project if scope == 'project' else '')
        with self.locked():
            forgotten = self._path('forgotten', ident + '.json')
            if forgotten.exists():
                if not data.get('restore_forgotten') or not isinstance(data.get('evidence'), dict) or data['evidence'].get('kind') != 'user':
                    raise MemoryFault('forgotten', '此语义键已被遗忘；仅在用户明确要求恢复时传restore_forgotten=true及新用户依据')
            prior = None
            path = self._path('notes', ident + '.md')
            if path.exists() and not forgotten.exists():
                prior = self._load(ident)
                if any(prior[k] != v for k, v in {'title': title, 'body': body, 'kind': kind, **fields}.items()):
                    raise MemoryFault('conflict', f"该语义键已有记忆，请read后用revise及expected_revision={prior['revision']}更正")
            sid, origin = self._source(data.get('evidence'))
            if prior and sid in prior['sources']:
                return {'id': ident, 'revision': prior['revision'], 'saved': True, 'unchanged': True}
            note = {'id': ident, 'key': key, 'title': title, 'body': body, 'scope': scope, 'project': partition, 'kind': kind, **fields,
                    'sources': list(dict.fromkeys((prior['sources'] if prior else []) + [sid])), 'created_at': prior['created_at'] if prior else utc(),
                    'updated_at': utc(), 'revision': prior['revision'] + 1 if prior else 1, 'muted': prior['muted'] if prior else False,
                    'status': 'provisional' if origin == 'inference' else 'active'}
            result = self._write(note, prior)
            forgotten.unlink(missing_ok=True)
            return result

    def _fields(self, data, project):
        aliases, related = data.get('aliases', []), data.get('related', [])
        if not isinstance(aliases, list) or len(aliases) > 12 or not isinstance(related, list) or len(related) > 12:
            raise MemoryFault('invalid', 'aliases/related须为最多12项的数组')
        aliases = list(dict.fromkeys(text(a, 'alias', 80) for a in aliases))
        for ident in related:
            self._get(ident, project)
        importance = data.get('importance', 2)
        if type(importance) is not int or not 1 <= importance <= 3:
            raise MemoryFault('invalid', 'importance须为1～3的整数')
        return {'aliases': aliases, 'related': list(dict.fromkeys(related)), 'importance': importance, 'expires_at': timestamp(data.get('expires_at', ''))}

    def revise(self, data, project):
        if set(data) - {'id', 'expected_revision', 'title', 'body', 'kind', 'aliases', 'related', 'importance', 'expires_at', 'evidence'}:
            raise MemoryFault('invalid', 'revise 包含未知字段')
        with self.locked():
            prior = self._get(data.get('id'), project)
            self._revision(prior, data)
            note = dict(prior)
            for key, maximum in [('title', 120), ('body', 6000)]:
                if key in data:
                    note[key] = text(data[key], key, maximum)
            if 'kind' in data:
                if data['kind'] not in KINDS:
                    raise MemoryFault('invalid', '无效的kind')
                note['kind'] = data['kind']
            note.update(self._fields({**prior, **data}, prior['project']))
            sid, origin = self._source(data.get('evidence'))
            note['sources'] = list(dict.fromkeys(prior['sources'] + [sid]))
            note.update(revision=prior['revision'] + 1, updated_at=utc(), status='provisional' if origin == 'inference' else 'active')
            return self._write(note, prior)

    def _revision(self, prior, data):
        if type(data.get('expected_revision')) is not int or data['expected_revision'] != prior['revision']:
            raise MemoryFault('conflict', f"版本已变化，read后以expected_revision={prior['revision']}重试")

    def manage(self, action, data, project):
        if set(data) - {'id', 'expected_revision'}:
            raise MemoryFault('invalid', '管理操作仅接受id、expected_revision')
        with self.locked():
            note = self._get(data.get('id'), project)
            self._revision(note, data)
            if action in ('mute', 'unmute'):
                prior = dict(note)
                note.update(muted=action == 'mute', revision=note['revision'] + 1, updated_at=utc())
                return self._write(note, prior)
            atomic(self._path('forgotten', note['id'] + '.json'), dump({'id': note['id'], 'at': utc()}))
            self._path('notes', note['id'] + '.md').unlink()
            history = self._path('history', note['id'])
            count = 0
            if history.exists():
                for file in history.glob('*.md'):
                    self._safe(file).unlink()
                    count += 1
                if not any(history.iterdir()):
                    history.rmdir()
            referenced = set()
            for p in self.directories['notes'].glob('m-*.md'):
                try:
                    referenced.update(self._load(p.stem)['sources'])
                except MemoryFault:
                    # 损坏笔记的引用不明，保留来源而不是误删。
                    referenced.update(note['sources'])
            removed = 0
            for sid in note['sources']:
                if sid not in referenced:
                    self._path('sources', sid + '.json').unlink(missing_ok=True)
                    removed += 1
            self._safe(self.index).unlink(missing_ok=True)
            return {'forgotten': note['id'], 'deleted_history': count, 'deleted_orphan_sources': removed, 'external_backups_retained': True}

    def _open_index(self):
        self._safe(self.index)
        con = sqlite3.connect(self.index, timeout=10)
        try:
            con.execute('CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, scope TEXT, project TEXT, kind TEXT, muted INTEGER, status TEXT, expires TEXT, updated TEXT, importance INTEGER, doc TEXT)')
            con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED,title,aliases,body)')
            return con
        except sqlite3.DatabaseError as error:
            con.close()
            if getattr(error, 'sqlite_errorcode', 0) not in (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT):
                raise
            self.index.unlink()
            return self._open_index()

    def _sync(self, con):
        self.warnings = []
        known = {row[0]: row[1:] for row in con.execute('SELECT id,mtime,size,doc FROM notes')}
        live = set()
        for path in self.directories['notes'].glob('m-*.md'):
            if self._path('forgotten', path.stem + '.json').exists():
                continue
            self._safe(path)
            st = path.stat()
            cached = known.get(path.stem)
            if cached and cached[:2] == (st.st_mtime_ns, st.st_size) and all(self._path('sources', sid + '.json').is_file() for sid in json.loads(cached[2])['sources']):
                live.add(path.stem)
                continue
            try:
                note = self._load(path.stem)
            except MemoryFault as error:
                self.warnings.append({'id': path.stem, 'code': error.code})
                continue
            live.add(note['id'])
            con.execute('DELETE FROM search WHERE id=?', (note['id'],))
            con.execute('INSERT OR REPLACE INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (note['id'], st.st_mtime_ns, st.st_size, note['scope'], note['project'], note['kind'], int(note['muted']), note['status'], note['expires_at'], note['updated_at'], note['importance'], dump(note)))
            con.execute('INSERT INTO search VALUES(?,?,?,?)', (note['id'], ' '.join(sorted(tokens(note['title']))), ' '.join(sorted(tokens(' '.join(note['aliases'])))), ' '.join(sorted(tokens(note['body'])))))
        for ident in known.keys() - live:
            con.execute('DELETE FROM notes WHERE id=?', (ident,))
            con.execute('DELETE FROM search WHERE id=?', (ident,))
        con.commit()

    def _brief(self, notes, limit=5, budget=1500):
        items = []
        for note in notes:
            if len(items) >= limit:
                break
            item = {key: note[key] for key in ('id', 'title', 'kind', 'scope', 'revision', 'status')}
            item['excerpt'] = note['body'][:600]
            item['sources'] = len(note['sources'])
            remaining = budget - len(dump(items + [item]))
            if remaining < 0:
                item['excerpt'] = item['excerpt'][:max(0, len(item['excerpt']) + remaining - 1)]
            if len(dump(items + [item])) > budget:
                break
            items.append(item)
        return {'items': items, 'budget_chars': budget, 'warnings': self.warnings[:5], 'warning_count': len(self.warnings), 'evidence_not_instructions': True}

    def search(self, query, project='', include_muted=False, context=False):
        query = text(query, 'query', 400, empty=True)
        with self.locked(), contextlib.closing(self._open_index()) as con:
            self._sync(con)
            where = "(n.scope='global' OR (?<>'' AND n.project=?)) AND (? OR n.muted=0) AND (n.expires='' OR n.expires>?)"
            params = [project, project, int(include_muted), utc()]
            if context:
                where += " AND n.kind IN ('preference','workflow') AND n.status='active'"
            words = sorted(tokens(query), key=lambda w: (-len(w), w))[:48]
            if words:
                expression = ' OR '.join('"' + w + '"' for w in words)
                rows = con.execute(f'SELECT n.doc FROM search JOIN notes n ON n.id=search.id WHERE search MATCH ? AND {where} ORDER BY bm25(search,0,8,5,1),n.importance DESC,n.updated DESC LIMIT 30', [expression] + params)
            else:
                rows = con.execute(f'SELECT n.doc FROM notes n WHERE {where} ORDER BY n.importance DESC,n.updated DESC LIMIT 30', params)
            return self._brief([json.loads(row[0]) for row in rows], limit=3 if context else 5, budget=500 if context else 1500)

    def read(self, ident, project):
        with self.locked():
            note = self._get(ident, project)
            return {'note': note, 'expired': bool(note['expires_at'] and note['expires_at'] <= utc()), 'evidence_not_instructions': True}

    def sources(self, ident, project):
        with self.locked():
            note = self._get(ident, project)
            sources = [json.loads(self._path('sources', s + '.json').read_text(encoding='utf-8')) for s in note['sources']]
            return {'id': ident, 'sources': sources, 'evidence_not_instructions': True}

    def related(self, ident, project):
        with self.locked():
            note = self._get(ident, project)
            linked = []
            for target in note['related']:
                try:
                    item = self._get(target, project)
                    if not item['muted'] and (not item['expires_at'] or item['expires_at'] > utc()):
                        linked.append(item)
                except MemoryFault:
                    continue
            return self._brief(linked)

    def status(self):
        with self.locked(), contextlib.closing(self._open_index()) as con:
            self._sync(con)
            return {'version': VERSION, 'root': str(self.root), 'notes': con.execute('SELECT count(*) FROM notes').fetchone()[0], 'sources': len(list(self.directories['sources'].glob('s-*.json'))), 'forgotten': len(list(self.directories['forgotten'].glob('m-*.json'))), 'warnings': self.warnings}

    def rebuild(self):
        with self.locked():
            self._safe(self.index).unlink(missing_ok=True)
        return self.status()

    def dispatch(self, action, data=None, project=''):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise MemoryFault('invalid', 'data须为JSON对象')
        project = project_key(project)
        if action == 'save':
            return self.save(data, project)
        if action == 'revise':
            return self.revise(data, project)
        if action in ('forget', 'mute', 'unmute'):
            return self.manage(action, data, project)
        if action in ('read', 'sources', 'related'):
            if set(data) != {'id'}:
                raise MemoryFault('invalid', '此操作只接受id')
            return getattr(self, action)(data['id'], project)
        if action in ('search', 'context', 'list'):
            if set(data) - {'query', 'include_muted'} or type(data.get('include_muted', False)) is not bool:
                raise MemoryFault('invalid', '仅接受query和include_muted')
            if action == 'search' and not text(data.get('query', ''), 'query', 400, empty=True):
                raise MemoryFault('invalid', 'search需要query，浏览请用list')
            return self.search(data.get('query', ''), project, data.get('include_muted', False), context=action == 'context')
        if action == 'status' and not data:
            return self.status()
        raise MemoryFault('invalid', '未知操作或多余参数')
