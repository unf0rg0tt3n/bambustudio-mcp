# Reusable presets: setup and limits

## What changes

The upstream project settings tools edit isolated 3MF copies. These new tools
instead write reusable Bambu Studio JSON presets. `kind` is exactly `process`
or `filament`; machine/printer profiles are not editable. Each call requires a
configured target ID, never a user-supplied host or filesystem path.

* `desktop`: `root/process/<name>.json`, `root/filament/<name>.json`; emits user
  deltas with `inherits` referencing an installed base by its **internal name**.
* `api`: `root/presets/<name>.json`, `root/filaments/<name>.json`; emits fully
  resolved settings with `inherits: ""`. This maps the maziggy slicing API's
  storage categories, not a nonexistent desktop configuration directory.
  That API's materializer normalizes `from: "User"` to CLI `from: "system"`.
  These files are intended for that materializer, not blindly passed directly
  to every Studio CLI version. Do not POST them through an overwriting upload
  endpoint when a local atomic filesystem write is available.

Names and scalar/vector IDs follow Bambu conventions: `print_settings_id` is a
string; `filament_settings_id` is a one-element string array. Material
`filament_id` is retained from the base; cloud `setting_id` is not copied.
Vendor resource JSON and user presets are indexed by their internal names,
not filenames (a support-material internal name may contain `/`). Newly created
filenames reject separators, hidden names, traversal and control characters.
Missing parents, inheritance cycles, duplicate internal names and incompatible
shapes fail closed. Derive from a concrete printer/nozzle-compatible profile,
not an abstract `@base` template. Explicit compatibility changes may narrow an
inherited list; changing identity/parent/cloud fields requires a new derivation.
The catalog validates typed overrides against supported ranges/enums. Vendor
defaults are preserved, not rewritten just to match a newer catalog.

## Local installation

```sh
python3 -m venv .venv
.venv/bin/pip install '.[test]'
.venv/bin/pytest -q
```

Make a private configuration outside git, mode 0600, using
`examples/targets.example.json` as a template. Set
`BAMBUSTUDIO_TARGETS_CONFIG=/absolute/private/targets.json` before launching
`bambustudio-mcp`. No configured file means **no preset targets**, not access to
arbitrary directories. Protect this environment variable and configuration as
administrator inputs; they define the storage authority of the service.

For each local target, `root` is an existing real absolute directory;
`system_roots` are existing vendor profile roots containing `process/` and
`filament/` directories. Mount only the resources for the target's actual
version and vendor. Symlink ancestors and preset-tree symlinks are refused.
Create the layout directories yourself before enabling writes. A desktop root
must be the correct active account/profile directory for that installation,
not merely a guessed application configuration root. Native Windows storage
is unsupported: the implementation requires POSIX directory FDs and flock.
Network mounts require working POSIX atomic rename/link and advisory locking;
SMB/NFS/cloud-sync semantics are not assumed safe.

`writable: true` explicitly enables actual writes. Start read-only, inspect
`list_targets`, `list_presets` and `read_preset`, then validate a dry-run. Use the
service user's UID/GID and minimum directory access; never chmod a whole user
home or mount all home directories for convenience.

## Container/stdin launch

```sh
docker build -t bambustudio-mcp:local .
export TARGETS_CONFIG=/absolute/private/targets.json
export PRESET_ROOT=/absolute/preset-storage
export BAMBU_DEFAULTS=/absolute/vendor-resources/BBL
docker compose -f compose.example.yaml config --quiet
docker compose -f compose.example.yaml run --rm -T mcp
```

The example target paths are **container paths**, not host paths. The default
UID/GID is 65532; set MCP_UID/MCP_GID to an existing unprivileged account that
can access the selected mounts. Config and vendor defaults are read-only;
only preset storage and the ephemeral workspace are writable. No Docker
socket, host network, privileged mode or published HTTP port is required.
An MCP client launches this command and keeps its stdin/stdout attached.
`docker compose up -d` alone does not create a usable MCP transport.
The image includes an SSH client, not Bambu Studio or a remote slicing runtime.

## SSH targets

The central service still hosts MCP. Install this package on each authorized
POSIX storage target so its noninteractive `python3` can import
`bambustudio_mcp.preset_agent`. A remote agent is a **one-request storage helper**,
not a second MCP service. Its private config contains local targets only.
Configure a dedicated SSH alias, account, identity, and independently verified
`known_hosts` in the central service's private SSH directory. Never disable
host-key checking or embed credentials in target JSON. The container can mount
that directory read-only; ensure files are readable only by the service UID.

The only remote command is `python3 -m bambustudio_mcp.preset_agent --config ...`;
arguments are shell-quoted from administrator config, and all preset names,
changes and actions travel as JSON on stdin. OpenSSH is invoked with an argv
array, BatchMode, strict host-key checking and bounded timeouts. Agent actions
are allowlisted and forwarding to another SSH target is refused. Both central
and remote target configs must enable writes. No host/path/command is supplied
through an MCP operation. The transport trusts the configured account and
remote helper; use a dedicated account/restricted key where possible. An SSH
outage fails the operation; a timeout after sending a write can mean it committed,
so read back before retrying. Laptop/desktop installation or connectivity is
not implied by a successful central build.

## Usage and safety

1. `list_targets()`; choose a target explicitly.
2. `list_presets(target, "filament")`; `read_preset` the correct material base.
3. `derive_preset(target, "filament", exact_base, "My PETG", {"nozzle_temperature": [250]})`.
   This temperature is an **API-shape example**, not a recommendation. Confirm
   exact spool, manufacturer specification, printer/nozzle and calibration.
4. Review returned raw `preset` and `validation`. `create_preset(target, preset)`
   previews; `dry_run=false` creates a new file only. It never replaces a name.
5. Process example: derive from a compatible process with
   `{"enable_support": true, "support_interface_filament": 2}`. The support slot
   value is a slicer/project slot convention, **not an AMS tray selection**.
   A support filament's material properties and assigning that filament to
   supports/interface are separate operations. Confirm project slot mapping.
6. To edit, read the current SHA256 `revision` and pass it as
   `expected_revision` to `update_preset`. Changes use typed JSON numbers,
   booleans and arrays; stored Bambu JSON retains string/vector encoding.
7. Every update/restore first saves an exact checksummed backup in the selected
   category's `.preset-backups/`. List it with `list_preset_backups`, then call
   `restore_preset` with its ID and the current revision. Restore is also a
   dry-run by default and backs up the current file before replacement.

Writes are directory-FD anchored, use O_NOFOLLOW, reject hard-linked files,
serialize cooperating writers with flock, fsync temporary data, atomically
link new files / replace existing files, and verify exact readback. Dry-runs
create no files, backups, directories or locks. Updates require a matching
revision and do not edit vendor defaults. Backups remain local; there is no
automatic pruning or off-host disaster recovery. The application does not
promise a transaction spanning different targets or files.

**Close the slicer and stop cloud sync before applying desktop edits.** This
code does not stop any app, synchronize GUI memory, sign into Bambu Cloud, write
cloud metadata sidecars, migrate databases or resolve cloud conflicts. Studio
may overwrite externally edited profiles on exit or cloud synchronization.
Reopen/restart Studio after applying, verify the profile appears in the correct
account/printer/nozzle filter, and explicitly select it. Some releases may require
GUI import of the JSON rather than directory discovery; that is not automated.
There is no GUI-load guarantee across all Studio versions. API servers that
cache profile lists may likewise require a reload; verify GET list/read before
slicing. Advisory locks do not stop unrelated GUI/cloud/API writers, so a tiny
external-writer race remains: quiescing those writers is mandatory.

## Verification scope

Tests include pinned upstream Bambu resource fixtures (process, Generic PETG HF,
Bambu support material and complete ancestor chains), both storage layouts,
identity/string shapes, inheritance, create/update/restore, stale revisions,
read-only/dry-run, multiple local targets, symlink/hardlink/traversal rejection,
concurrent creation, SSH argv/payload injection checks, subprocess agent
roundtrips and MCP registration/calls. Fixture sources and exact SHA256 values
are in `tests/fixtures/manifest.json`; original profiles are Bambu Studio
AGPL-3.0 resources at the listed commit. No fabricated preset fixtures are used
as evidence of vendor compatibility.

SSH transport invocation tests mock the network boundary; subprocess integration
executes the actual agent. Passing those tests is **not evidence of connectivity
to any particular workstation**. Validation is structural/inheritance/catalog
validation, not a Studio GUI import, successful physical print, correct AMS
mapping, material calibration or mechanical-strength proof. Never start a print
as a preset-management test. Orca layout may be compatible, but catalog/version
support is Bambu-focused; Orca-specific edits need separate runtime validation.
