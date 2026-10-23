"""Allowlisted, offline reusable presets. No printer or slicer execution.

POSIX storage only. Directory FDs and O_NOFOLLOW prevent symlink escapes;
flock serializes cooperating writers. Studio/cloud writers must be stopped.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import uuid

from .catalog import Catalog

KINDS = ('process', 'filament')
META = {'type', 'name', 'inherits', 'from', 'instantiation', 'version', 'setting_id',
        'filament_id', 'print_settings_id', 'filament_settings_id', 'description'}
IMMUTABLE = META - {'description'}
LIMIT = 8 * 1024 * 1024


def blob(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def filename(name, layout='desktop'):
    if not isinstance(name, str) or not name.strip() or len(name.encode()) > 180 or name in {'.', '..'} or name.startswith('.') or any(c in name for c in '/\\<>:"|?*') or any(ord(c) < 32 for c in name) or name.endswith((' ', '.')):
        raise ValueError('Unsafe preset filename')
    if layout == 'api' and not re.fullmatch(r'[A-Za-z0-9]{1,180}', name):
        raise ValueError('API preset names must contain only ASCII letters and digits so GET /profiles/:category/:name can read them back')
    return name + '.json'


@contextlib.contextmanager
def directory(path):
    """Open every ancestor without following links; keep the final inode pinned."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Storage roots must be absolute, without traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def read_bytes(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
            raise ValueError('Unsafe or oversized preset file')
        raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Oversized preset')
        return raw


def atomic(fd, name, raw, replace=False):
    temporary = '.preset-' + uuid.uuid4().hex
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def scan(fd, prefix='', depth=0):
    if depth > 12:
        raise ValueError('Preset tree too deep')
    for name in sorted(os.listdir(fd)):
        if name.startswith('.'):
            continue
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Symlinks are not allowed in preset trees')
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                yield from scan(child, prefix + name + '/', depth + 1)
            finally:
                os.close(child)
        elif name.endswith('.json') and not name.startswith('.'):
            yield prefix + name, read_bytes(fd, name)


class Presets:
    def __init__(self, config=None):
        if config is None:
            path = os.environ.get('BAMBUSTUDIO_TARGETS_CONFIG')
            config = json.loads(Path(path).read_text()) if path else {'targets': {}}
        self.targets = config['targets']
        self.catalog = Catalog()
        for key, target in self.targets.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key):
                raise ValueError('Invalid target identifier')
            if target.get('transport', 'local') not in {'local', 'ssh'}:
                raise ValueError('Unknown target transport')
            if target.get('layout', 'desktop') not in {'desktop', 'api'}:
                raise ValueError('Unknown target layout')
            if target.get('transport') == 'ssh':
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', target['host']):
                    raise ValueError('SSH host must be a configured alias, not options or shell text')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', target['remote_target']):
                    raise ValueError('Invalid remote target')
                if not Path(target['remote_config']).is_absolute() or '\n' in target['remote_config']:
                    raise ValueError('Remote configuration must be an absolute path')

    def list_targets(self):
        return {'targets': [{'id': key, 'transport': value.get('transport', 'local'),
                             'layout': value.get('layout', 'desktop'), 'writable': value.get('writable', False)}
                            for key, value in self.targets.items()]}

    def _target(self, target):
        if target not in self.targets:
            raise ValueError('Target not allowlisted')
        return self.targets[target]

    def _remote(self, target, action, **arguments):
        config = self._target(target)
        if config.get('transport') != 'ssh':
            return None
        if action in {'create', 'update', 'restore'} and not arguments.get('dry_run', True) and not config.get('writable', False):
            raise ValueError('Target is read-only; administrator must explicitly enable writes')
        command = shlex.join(['python3', '-m', 'bambustudio_mcp.preset_agent', '--config', config['remote_config']])
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                 '-o', 'ConnectTimeout=10', '--', config['host'], command],
                                input=json.dumps({'target': config['remote_target'], 'action': action, **arguments}),
                                text=True, capture_output=True, timeout=60, check=False)
        if result.returncode:
            raise ValueError('Remote preset operation failed; check target connectivity/configuration')
        response = json.loads(result.stdout)
        if 'error' in response:
            raise ValueError(response['error'])
        return response['result']

    def _folder(self, target, kind):
        if kind not in KINDS:
            raise ValueError('Only process and filament presets are supported')
        config = self._target(target)
        leaf = {'process': 'presets', 'filament': 'filaments'}[kind] if config.get('layout') == 'api' else kind
        return Path(config['root']) / leaf

    def _index(self, target, kind):
        config = self._target(target)
        index = {}
        locations = [(Path(root) / kind, 'system') for root in config.get('system_roots', [])]
        locations.append((self._folder(target, kind), 'user'))
        for root, origin in locations:
            with directory(root) as fd:
                for relative, raw in scan(fd):
                    data = json.loads(raw)
                    if origin == 'system' and (not isinstance(data, dict) or not data.get('name')):
                        continue
                    name = data.get('name')
                    if not name or data.get('type', kind) != kind:
                        raise ValueError('Invalid preset identity in target')
                    if name in index:
                        raise ValueError('Ambiguous preset name: ' + name)
                    index[name] = {'preset': data, 'revision': digest(raw), 'origin': origin, 'file': relative}
        return index

    def _resolve(self, index, name, seen=()):
        if name in seen or len(seen) > 32:
            raise ValueError('Cyclic or excessive inheritance')
        if name not in index:
            raise ValueError('Missing inherited preset: ' + name)
        data = index[name]['preset']
        parent = data.get('inherits')
        effective = self._resolve(index, parent, (*seen, name)) if parent else {}
        return {**effective, **data}

    def list(self, target, kind):
        remote = self._remote(target, 'list', kind=kind)
        if remote is not None:
            return remote
        return {'presets': [{'name': name, 'kind': kind, 'origin': row['origin'], 'revision': row['revision']}
                            for name, row in self._index(target, kind).items()]}

    def read(self, target, kind, name):
        remote = self._remote(target, 'read', kind=kind, name=name)
        if remote is not None:
            return remote
        index = self._index(target, kind)
        if name not in index:
            raise ValueError('Unknown preset')
        return {**index[name], 'effective': self._resolve(index, name)}

    def _changes(self, kind, effective, changes):
        result = {}
        for key, value in changes.items():
            if key in IMMUTABLE or key not in effective or key in {'compatible_printers_condition', 'compatible_prints_condition'}:
                raise ValueError('Cannot change identity, inheritance, conditions or unknown setting: ' + key)
            if key == 'description':
                if not isinstance(value, str):
                    raise ValueError('Description must be text')
                result[key] = value
                continue
            if key == 'compatible_printers':
                if not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in effective[key] for v in value):
                    raise ValueError('Compatibility may only narrow the inherited printer list')
                result[key] = value
                continue
            if kind == 'filament' and key in {'support_filament', 'support_interface_filament', 'enable_support'}:
                raise ValueError('Support slot assignment belongs to process, not filament')
            self.catalog.validate(key, value, 'project')
            result[key] = self.catalog.encode(key, value)
        return result

    def derive(self, target, kind, base, name, changes):
        remote = self._remote(target, 'derive', kind=kind, base=base, name=name, changes=changes)
        if remote is not None:
            return remote
        layout = self._target(target).get('layout', 'desktop')
        filename(name, layout)
        index = self._index(target, kind)
        if name in index:
            raise ValueError('Preset already exists')
        effective = self._resolve(index, base)
        if effective.get('instantiation') != 'true':
            raise ValueError('Derive from an instantiable printer-compatible preset, not an abstract base')
        if not effective.get('compatible_printers'):
            raise ValueError('Base must declare compatible printers')
        # Flatten only for CLI/API storage, whose loader has no user-preset inheritance registry.
        draft = {k: v for k, v in effective.items() if k not in META} if layout == 'api' else {}
        draft.update(type=kind, name=name, inherits='' if layout == 'api' else base,
                     **{'from': 'User', 'instantiation': 'true'})
        if 'version' in effective:
            draft['version'] = effective['version']
        if kind == 'filament':
            draft['filament_settings_id'] = [name]
            draft['filament_id'] = effective.get('filament_id', '')
        else:
            draft['print_settings_id'] = name
        draft.update(self._changes(kind, effective, changes))
        return {'preset': draft, 'base_revision': index[base]['revision'],
                'validation': self.validate(target, draft)}

    def validate(self, target, preset):
        remote = self._remote(target, 'validate', preset=preset)
        if remote is not None:
            return remote
        data = copy.deepcopy(preset)
        kind = data.get('type')
        self._folder(target, kind)
        name = data.get('name')
        filename(name, self._target(target).get('layout', 'desktop'))
        if data.get('from') != 'User' or data.get('instantiation') != 'true' or 'setting_id' in data:
            raise ValueError('User preset requires from=User, instantiation=true and no cloud setting_id')
        identity = 'filament_settings_id' if kind == 'filament' else 'print_settings_id'
        if data.get(identity) != ([name] if kind == 'filament' else name):
            raise ValueError('Settings ID must match preset name and Bambu scalar/vector shape')
        if kind == 'filament' and (not isinstance(data.get('filament_id'), str) or not data['filament_id']):
            raise ValueError('Filament material ID must be retained')
        index = self._index(target, kind)
        parent = data.get('inherits')
        if self._target(target).get('layout') != 'api' and not parent:
            raise ValueError('Desktop preset must inherit an installed preset')
        if parent == name:
            raise ValueError('Cyclic inheritance')
        inherited = self._resolve(index, parent) if parent else {}
        if kind == 'filament' and parent and data['filament_id'] != inherited.get('filament_id'):
            raise ValueError('Filament material ID differs from inherited material')
        # Validate edited overrides, not trusted vendor defaults (version drift is possible).
        schema = inherited if parent else {k: v for row in index.values() if row['origin'] == 'system' for k, v in row['preset'].items()}
        for key, value in data.items():
            if key in META:
                continue
            if key not in schema:
                raise ValueError('Unknown setting for profile type: ' + key)
            if parent and value == inherited.get(key):
                continue
            if not parent and any(value == row['preset'].get(key) for row in index.values() if row['origin'] == 'system'):
                continue
            if key == 'compatible_printers':
                if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value) or (parent and not set(value) <= set(inherited.get(key, []))):
                    raise ValueError('Invalid or broadened compatibility')
                continue
            option = self.catalog.options.get(key)
            if not option:
                raise ValueError('Setting not in supported catalog: ' + key)
            vector = isinstance(schema[key], list)
            if vector != isinstance(value, list) or (vector and (not value or not all(isinstance(v, str) for v in value))) or (not vector and not isinstance(value, str)):
                raise ValueError('Bambu JSON settings must retain string/scalar/vector shape')
            if option['type'] in {'Bool', 'Bools'} and any(v not in {'0', '1'} for v in (value if vector else [value])):
                raise ValueError('Bambu booleans must be 0 or 1')
            self.catalog.validate(key, self.catalog.decode(key, value), 'project')
        index[name] = {'preset': data}
        effective = self._resolve(index, name)
        if not effective.get('compatible_printers'):
            raise ValueError('No explicit compatible printers')
        self.catalog.validate_combination({k: self.catalog.decode(k, v) for k, v in effective.items() if k in self.catalog.options})
        return {'valid': True, 'kind': kind, 'compatible_printers': effective['compatible_printers'],
                'evidence': 'structural/inheritance/catalog checks; not a Studio GUI load or physical calibration'}

    def create(self, target, preset, dry_run=True):
        remote = self._remote(target, 'create', preset=preset, dry_run=dry_run)
        return remote if remote is not None else self._write(target, preset, None, dry_run)

    def _write(self, target, preset, expected_revision, dry_run):
        config = self._target(target)
        if not dry_run and not config.get('writable', False):
            raise ValueError('Target is read-only; administrator must explicitly enable writes')
        kind, name = preset['type'], preset['name']
        file = filename(name, config.get('layout', 'desktop'))
        with directory(self._folder(target, kind)) as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            validation = self.validate(target, preset)
            try:
                old = read_bytes(fd, file)
            except FileNotFoundError:
                old = None
            if expected_revision is None:
                if old is not None or name in self._index(target, kind):
                    raise ValueError('Preset already exists; use explicit update')
            elif old is None or digest(old) != expected_revision:
                raise ValueError('Stale revision; read current preset before update')
            raw = blob(preset)
            result = {'preset': preset, 'revision': digest(raw), 'dry_run': dry_run, 'validation': validation}
            if dry_run:
                return result
            if old is not None:
                result['backup'] = self._backup(fd, name, kind, old)
                if read_bytes(fd, file) != old:
                    raise ValueError('Concurrent external modification; write cancelled')
            atomic(fd, file, raw, replace=old is not None)
            if read_bytes(fd, file) != raw:
                raise ValueError('Readback mismatch')
            result['verified'] = True
            return result

    def update(self, target, kind, name, changes, expected_revision, dry_run=True):
        remote = self._remote(target, 'update', kind=kind, name=name, changes=changes, expected_revision=expected_revision, dry_run=dry_run)
        if remote is not None:
            return remote
        row = self.read(target, kind, name)
        if row['origin'] != 'user' or row['file'] != filename(name, self._target(target).get('layout', 'desktop')):
            raise ValueError('Only flat user presets may be updated')
        draft = {**row['preset'], **self._changes(kind, row['effective'], changes)}
        return self._write(target, draft, expected_revision, dry_run)

    def _backup(self, fd, name, kind, raw):
        try:
            os.mkdir('.preset-backups', 0o700, dir_fd=fd)
        except FileExistsError:
            pass
        child = os.open('.preset-backups', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            record = blob({'name': name, 'kind': kind, 'raw': raw.decode(), 'revision': digest(raw)})
            backup_id = digest(record)
            try:
                atomic(child, backup_id + '.json', record)
            except FileExistsError:
                if read_bytes(child, backup_id + '.json') != record:
                    raise ValueError('Backup collision/corruption')
            return backup_id
        finally:
            os.close(child)

    def backups(self, target, kind, name):
        remote = self._remote(target, 'backups', kind=kind, name=name)
        if remote is not None:
            return remote
        filename(name, self._target(target).get('layout', 'desktop'))
        result = []
        try:
            with directory(self._folder(target, kind) / '.preset-backups') as fd:
                for file in os.listdir(fd):
                    if not re.fullmatch(r'[0-9a-f]{64}\.json', file):
                        raise ValueError('Unexpected backup file')
                    raw = read_bytes(fd, file)
                    if digest(raw) != file[:-5]:
                        raise ValueError('Backup checksum mismatch')
                    record = json.loads(raw)
                    if record['name'] == name and record['kind'] == kind:
                        result.append({'backup_id': file[:-5], 'revision': record['revision']})
        except FileNotFoundError:
            pass
        return {'backups': result}

    def restore(self, target, kind, name, backup_id, expected_revision, dry_run=True):
        remote = self._remote(target, 'restore', kind=kind, name=name, backup_id=backup_id, expected_revision=expected_revision, dry_run=dry_run)
        if remote is not None:
            return remote
        if not re.fullmatch(r'[0-9a-f]{64}', backup_id):
            raise ValueError('Invalid backup ID')
        with directory(self._folder(target, kind) / '.preset-backups') as fd:
            raw = read_bytes(fd, backup_id + '.json')
        record = json.loads(raw)
        if digest(raw) != backup_id or record['name'] != name or record['kind'] != kind or digest(record['raw'].encode()) != record['revision']:
            raise ValueError('Backup identity/checksum mismatch')
        return self._write(target, json.loads(record['raw']), expected_revision, dry_run)
