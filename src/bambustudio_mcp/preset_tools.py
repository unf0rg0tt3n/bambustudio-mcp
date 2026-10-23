"""Reusable preset MCP tools, separate from project-setting tools."""
import asyncio
from mcp.types import ToolAnnotations
from .presets import Presets


def register(mcp, presets=None):
    api = presets or Presets()
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

    @mcp.tool(annotations=read)
    def list_targets() -> dict:
        """List configured target IDs; no arbitrary paths, hosts or commands accepted."""
        return api.list_targets()

    @mcp.tool(annotations=read)
    async def list_presets(target: str, kind: str) -> dict:
        """List reusable process or filament presets, not project-local settings."""
        return await asyncio.to_thread(api.list, target, kind)

    @mcp.tool(annotations=read)
    async def read_preset(target: str, kind: str, name: str) -> dict:
        """Read raw JSON, resolved inheritance and SHA256 revision."""
        return await asyncio.to_thread(api.read, target, kind, name)

    @mcp.tool(annotations=read)
    async def derive_preset(target: str, kind: str, base: str, name: str, changes: dict) -> dict:
        """Draft a reusable preset from an installed compatible base; does not write.

        changes uses typed numbers, booleans and arrays. Filament describes material;
        support_filament/support_interface_filament select slots in a PROCESS preset.
        Confirm actual spool, printer, nozzle and slot mapping; defaults are not calibration.
        """
        return await asyncio.to_thread(api.derive, target, kind, base, name, changes)

    @mcp.tool(annotations=read)
    async def validate_preset(target: str, preset: dict) -> dict:
        """Validate Bambu JSON shape/inheritance/compatibility/catalog; not GUI or print proof."""
        return await asyncio.to_thread(api.validate, target, preset)

    @mcp.tool(annotations=write)
    async def create_preset(target: str, preset: dict, dry_run: bool = True) -> dict:
        """Create a new reusable preset only; never overwrite. Preview by default."""
        return await asyncio.to_thread(api.create, target, preset, dry_run)

    @mcp.tool(annotations=write)
    async def update_preset(target: str, kind: str, name: str, changes: dict, expected_revision: str, dry_run: bool = True) -> dict:
        """Explicit update with SHA256 optimistic concurrency, backup, atomic write/readback.

        Only user presets. Close Studio/disable cloud sync first; no GUI synchronization.
        """
        return await asyncio.to_thread(api.update, target, kind, name, changes, expected_revision, dry_run)

    @mcp.tool(annotations=read)
    async def list_preset_backups(target: str, kind: str, name: str) -> dict:
        """List checksummed automatic pre-update backups for this preset."""
        return await asyncio.to_thread(api.backups, target, kind, name)

    @mcp.tool(annotations=write)
    async def restore_preset(target: str, kind: str, name: str, backup_id: str, expected_revision: str, dry_run: bool = True) -> dict:
        """Restore one backup with current revision guard; backs up current contents first."""
        return await asyncio.to_thread(api.restore, target, kind, name, backup_id, expected_revision, dry_run)
