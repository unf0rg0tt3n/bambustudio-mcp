import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from test_presets import store


def create(api, kind='filament', name='My PETG', base='Generic PETG HF @BBL X1C'):
    draft = api.derive('test', kind, base, name, {})['preset']
    return api.create('test', draft, dry_run=False)


def test_filament_update_backup_restore(tmp_path):
    api, root = store(tmp_path)
    first = create(api)
    assert first['preset']['filament_settings_id'] == ['My PETG']
    assert first['preset']['filament_id'].startswith('G')
    second = api.update('test', 'filament', 'My PETG', {'nozzle_temperature': [250]}, first['revision'], False)
    assert api.read('test', 'filament', 'My PETG')['effective']['nozzle_temperature'] == ['250']
    assert api.backups('test', 'filament', 'My PETG')['backups'][0]['backup_id'] == second['backup']
    restored = api.restore('test', 'filament', 'My PETG', second['backup'], second['revision'], False)
    assert restored['preset'] == first['preset']
    with pytest.raises(ValueError, match='Stale'):
        api.update('test', 'filament', 'My PETG', {'nozzle_temperature': [260]}, second['revision'], False)


def test_support_material_not_slot_assignment(tmp_path):
    api, _ = store(tmp_path)
    profile = create(api, name='My support', base='Bambu Support For PLA/PETG @BBL X1C')
    assert api.read('test', 'filament', 'My support')['effective']['filament_is_support'] == ['1']
    with pytest.raises(ValueError):
        api.update('test', 'filament', 'My support', {'support_interface_filament': 2}, profile['revision'])
    process = api.derive('test', 'process', '0.20mm Standard @BBL X1C', 'Support process', {'enable_support': True, 'support_interface_filament': 2})
    assert process['preset']['support_interface_filament'] == '2'


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/b', 'a\\b', '.hidden', 'a\x00b', 'name.'])
def test_bad_names(tmp_path, name):
    api, _ = store(tmp_path)
    with pytest.raises(ValueError):
        create(api, name=name)


def test_dry_run_and_readonly(tmp_path):
    api, root = store(tmp_path)
    api.targets['test']['writable'] = False
    draft = api.derive('test', 'filament', 'Generic PETG HF @BBL X1C', 'Preview', {})['preset']
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    assert api.create('test', draft)['dry_run']
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == before
    with pytest.raises(ValueError, match='read-only'):
        api.create('test', draft, False)
    with pytest.raises(ValueError, match='allowlisted'):
        api.list('unknown', 'filament')


def test_symlink_and_hardlink_refused(tmp_path):
    api, root = store(tmp_path)
    outside = tmp_path / 'secret.json'
    outside.write_text('{}')
    link = root / 'filament' / 'secret.json'
    link.symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        api.list('test', 'filament')
    link.unlink()
    os.link(outside, link)
    with pytest.raises(ValueError, match='Unsafe'):
        api.list('test', 'filament')
    assert outside.read_text() == '{}'


def test_bad_schema_and_inheritance(tmp_path):
    api, _ = store(tmp_path)
    draft = api.derive('test', 'filament', 'Generic PETG HF @BBL X1C', 'Draft', {})['preset']
    for change in [{'filament_settings_id': 'Draft'}, {'inherits': 'missing'}, {'inherits': 'Draft'},
                   {'filament_id': 'OTHER'}, {'setting_id': 'cloud'}, {'nozzle_temperature': [99999]},
                   {'nozzle_temperature': ['nan']}, {'support_interface_filament': '2'},
                   {'compatible_printers': ['Unknown printer']}]:
        with pytest.raises((ValueError, TypeError)):
            api.validate('test', {**draft, **change})


def test_multi_target_api_flattened(tmp_path):
    api, root = store(tmp_path)
    api_root = tmp_path / 'api'
    api_root.mkdir()
    (api_root / 'presets').mkdir()
    (api_root / 'filaments').mkdir()
    api.targets['api'] = {**api.targets['test'], 'root': str(api_root), 'layout': 'api'}
    with pytest.raises(ValueError, match='ASCII letters and digits'):
        api.derive('api', 'filament', 'Generic PETG HF @BBL X1C', 'API profile', {})
    for kind, base in [('filament', 'Generic PETG HF @BBL X1C'), ('process', '0.20mm Standard @BBL X1C')]:
        draft = api.derive('api', kind, base, 'APIProfile1', {})['preset']
        assert draft['inherits'] == ''
        result = api.create('api', draft, False)
        assert result['verified']
        assert api.read('api', kind, 'APIProfile1')['effective']['compatible_printers']
        assert not any(p['name'] == 'APIProfile1' for p in api.list('test', kind)['presets'])
    assert (api_root / 'presets' / 'APIProfile1.json').is_file()
    assert (api_root / 'filaments' / 'APIProfile1.json').is_file()


def test_concurrent_creates_only_one(tmp_path):
    api, _ = store(tmp_path)
    draft = api.derive('test', 'filament', 'Generic PETG HF @BBL X1C', 'Race', {})['preset']
    def write(_):
        try:
            return api.create('test', draft, False)['verified']
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [False, True]


def test_ssh_fixed_command_and_payload(tmp_path):
    from bambustudio_mcp.presets import Presets
    target = {'transport': 'ssh', 'host': 'studio-host', 'remote_target': 'local', 'remote_config': '/etc/presets/targets.json'}
    api = Presets({'targets': {'remote': target}})
    with patch('bambustudio_mcp.presets.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{"result": {"ok": true}}')) as run:
        assert api.read('remote', 'filament', '$(touch /evil); " quoted')['ok']
        args, kwargs = run.call_args
        assert '$(touch' not in ' '.join(args[0])
        assert json.loads(kwargs['input'])['name'].startswith('$(touch')
        assert 'StrictHostKeyChecking=yes' in args[0]
        assert not kwargs.get('shell')
    for host in ['-oProxyCommand=evil', 'host;id', 'user@host', 'host\ncommand']:
        with pytest.raises(ValueError):
            Presets({'targets': {'remote': {**target, 'host': host}}})


def test_ssh_readonly_cannot_be_bypassed():
    from bambustudio_mcp.presets import Presets
    api = Presets({'targets': {'remote': {'transport': 'ssh', 'host': 'host', 'remote_target': 'local', 'remote_config': '/etc/presets.json', 'writable': False}}})
    with patch('bambustudio_mcp.presets.subprocess.run') as run:
        with pytest.raises(ValueError, match='read-only'):
            api.create('remote', {}, False)
        run.assert_not_called()


def test_agent_allowlist_no_commands(tmp_path):
    from bambustudio_mcp.preset_agent import dispatch
    api, _ = store(tmp_path)
    with pytest.raises(ValueError):
        dispatch(api, {'target': 'test', 'action': '__getattribute__', 'name': 'targets'})
    assert dispatch(api, {'target': 'test', 'action': 'list', 'kind': 'process'})['presets']
