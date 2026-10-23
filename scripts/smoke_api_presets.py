"""Opt-in isolated maziggy API/CLI test. Run ONLY in a disposable container.

Example (source mounted read-only, no persistent user data/network):
 docker run --rm --network none --cpus 2 --memory 4g \
   --tmpfs /scratch:rw,exec,size=1g -e TMPDIR=/scratch -e PYTHONPATH=/work/src \
   -v "$PWD:/work:ro" --entrypoint python3 API_IMAGE \
   /work/scripts/smoke_api_presets.py

Creates process/PETG/support profiles from THIS image's bundled defaults,
checks the actual HTTP GET route, and slices a tiny cube through sliceModel.
No printer/network connection is possible. Never mount real preset storage.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from bambustudio_mcp.presets import Presets


def cube():
    vertices = [(x, y, z) for z in (0, 5) for y in (0, 5) for x in (0, 5)]
    faces = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
             (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return b'Preset compatibility test'.ljust(80, b'\0') + struct.pack('<I', len(faces)) + b''.join(
        struct.pack('<12fH', 0, 0, 0, *(value for index in face for value in vertices[index]), 0) for face in faces)


def main():
    defaults = Path(os.environ.get('BUNDLED_PROFILES_PATH', '/app/squashfs-root/resources/profiles/BBL'))
    with tempfile.TemporaryDirectory(prefix='preset-smoke-') as work:
        root = Path(work)
        for category in ('presets', 'filaments', 'printers'):
            (root / category).mkdir()
        api = Presets({'targets': {'smoke': {'layout': 'api', 'root': work, 'system_roots': [str(defaults)], 'writable': True}}})
        plans = [('process', '0.20mm Standard @BBL X1C', 'SmokeProcess', {'layer_height': 0.2}),
                 ('filament', 'Generic PETG @BBL X1C', 'SmokePETG', {}),
                 ('filament', 'Bambu Support For PLA/PETG @BBL X1C', 'SmokeSupport', {})]
        # Releases may rename PETG to PETG HF; select only a real compatible base.
        names = {p['name'] for p in api.list('smoke', 'filament')['presets']}
        if plans[1][1] not in names:
            plans[1] = ('filament', 'Generic PETG HF @BBL X1C', 'SmokePETG', {})
        for kind, base, name, changes in plans:
            preset = api.derive('smoke', kind, base, name, changes)['preset']
            assert not preset['inherits'] and preset['compatible_printers']
            result = api.create('smoke', preset, dry_run=False)
            assert api.read('smoke', kind, name)['revision'] == result['revision']
            print(json.dumps({'preset': name, 'revision': result['revision'], 'resolved_fields': len(preset)}), flush=True)
        shutil.copyfile(defaults / 'machine' / 'Bambu Lab X1 Carbon 0.4 nozzle.json', root / 'printers' / 'SmokePrinter.json')
        (root / 'cube.stl').write_bytes(cube())
        environment = dict(os.environ, DATA_PATH=work, BUNDLED_PROFILES_PATH=str(defaults))
        script = r'''
import express from '/app/node_modules/express/index.js';
import router from '/app/dist/routes/profiles/route.js';
import {sliceModel} from '/app/dist/routes/slicing/slicing.service.js';
import {resolveProfile, ensureProfileType, normalizeFromField} from '/app/dist/routes/slicing/profile-resolver.js';
import {readFile,stat,rm} from 'node:fs/promises';
const app=express(); app.use('/profiles',router);
const server=app.listen(0,'127.0.0.1');
await new Promise(resolve=>server.once('listening',resolve));
try {
 for (const [category,name,kind] of [['presets','SmokeProcess','process'],['filaments','SmokePETG','filament'],['filaments','SmokeSupport','filament']]) {
  const response=await fetch(`http://127.0.0.1:${server.address().port}/profiles/${category}/${encodeURIComponent(name)}`);
  if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
  const profile=await response.json();
  if(profile.name!==name || profile.type!==kind) throw Error(`Wrong readback: ${JSON.stringify(profile)}`);
  const resolved=normalizeFromField(ensureProfileType(await resolveProfile(profile,kind,{bundledProfilesPath:process.env.BUNDLED_PROFILES_PATH}),kind));
  if(resolved.from!=='system' || resolved.inherits) throw Error('Unresolved CLI profile');
  console.log(JSON.stringify({http_readback:name,status:response.status,cli_from:resolved.from}));
 }
 const result=await sliceModel(await readFile(`${process.env.DATA_PATH}/cube.stl`),'cube.stl',
  {printer:'SmokePrinter',preset:'SmokeProcess',filament:'SmokePETG',exportType:'3mf',bedType:'Textured PEI Plate',arrange:true});
 if(!result.gcodes.length) throw Error(`No slice artifact: ${result.workdir}`);
 for(const file of result.gcodes) {const info=await stat(file); if(!info.size)throw Error('Empty artifact'); console.log(JSON.stringify({slice_artifact:file.split('/').pop(),bytes:info.size}));}
 await rm(result.workdir,{recursive:true});
} finally {server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));}
'''
        subprocess.run(['node', '--input-type=module', '-e', script], env=environment, check=True, timeout=180)


if __name__ == '__main__':
    main()
