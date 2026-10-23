import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from test_presets import store


def test_fixture_provenance():
    folder = Path(__file__).parent / 'fixtures'
    manifest = json.loads((folder / 'manifest.json').read_text())
    for file, expected in manifest['files'].items():
        assert hashlib.sha256((folder / 'bambu' / file).read_bytes()).hexdigest() == expected


def test_agent_real_subprocess_roundtrip(tmp_path):
    api, _ = store(tmp_path)
    config = tmp_path / 'targets.json'
    config.write_text(json.dumps({'targets': api.targets}))
    def call(action, **args):
        process = subprocess.run([sys.executable, '-m', 'bambustudio_mcp.preset_agent', '--config', str(config)],
                                 input=json.dumps({'action': action, 'target': 'test', **args}), text=True, capture_output=True)
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)['result']
    for kind, base in [('process', '0.20mm Standard @BBL X1C'), ('filament', 'Generic PETG HF @BBL X1C')]:
        draft = call('derive', kind=kind, base=base, name='Agent test', changes={})['preset']
        result = call('create', preset=draft, dry_run=False)
        assert call('read', kind=kind, name='Agent test')['revision'] == result['revision']


def test_mcp_registration_and_real_tool_calls(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from bambustudio_mcp.preset_tools import register
    api, _ = store(tmp_path)
    mcp = FastMCP('test')
    register(mcp, api)
    async def check():
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {'list_targets', 'list_presets', 'read_preset', 'derive_preset', 'create_preset', 'update_preset', 'validate_preset', 'list_preset_backups', 'restore_preset'}
        result = await mcp.call_tool('derive_preset', {'target': 'test', 'kind': 'process', 'base': '0.20mm Standard @BBL X1C', 'name': 'MCP process', 'changes': {'layer_height': 0.16}})
        # FastMCP returns content and structured result in supported MCP versions.
        structured = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
        assert structured['preset']['layer_height'] == '0.16'
    asyncio.run(check())
