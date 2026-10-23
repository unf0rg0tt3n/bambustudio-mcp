import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / 'fixtures' / 'bambu'


def store(tmp_path, layout='desktop'):
    from bambustudio_mcp.presets import Presets
    root = tmp_path / 'user'
    root.mkdir()
    for kind in (('process', 'filament') if layout == 'desktop' else ('presets', 'filaments')):
        (root / kind).mkdir()
    defaults = tmp_path / 'defaults'
    shutil.copytree(FIXTURES, defaults)
    return Presets({'targets': {'test': {'root': str(root), 'system_roots': [str(defaults)], 'layout': layout, 'writable': True}}}), root


def test_derive_create_read_real_process(tmp_path):
    api, root = store(tmp_path)
    draft = api.derive('test', 'process', '0.20mm Standard @BBL X1C', 'My process', {'layer_height': 0.24, 'support_filament': 0})
    assert draft['preset']['inherits'] == '0.20mm Standard @BBL X1C'
    assert draft['preset']['print_settings_id'] == 'My process'
    assert draft['preset']['layer_height'] == '0.24'
    assert draft['preset']['from'] == 'User'
    assert 'setting_id' not in draft['preset']
    api.create('test', draft['preset'], dry_run=True)
    assert not (root / 'process' / 'My process.json').exists()
    saved = api.create('test', draft['preset'], dry_run=False)
    read = api.read('test', 'process', 'My process')
    assert saved['revision'] == read['revision']
    assert read['effective']['layer_height'] == '0.24'
    assert 'Bambu Lab P1S 0.4 nozzle' in read['effective']['compatible_printers']
    with pytest.raises(ValueError, match='exists'):
        api.create('test', draft['preset'], dry_run=False)
