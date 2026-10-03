"""Internal bounded source excerpts and active-application lookup."""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.tools import repo_store as _rs


def _get_active_app(tool_context: ToolContext) -> str | None:
    """Return the active application slug from session state, or None."""
    return tool_context.state.get("active_app")


async def _read_source_excerpt(
    path: str,
    start_line: int | None,
    end_line: int | None,
    tool_context: ToolContext,
) -> dict:
    """Fallback after graph retrieval leaves a specific behavior/version gap.

    Read one trusted graph/search/build path and a narrow explicit line range.
    Validates the manifest and clamps the window. Do not scan files for orientation.
    """
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    manifest_paths = {f["path"] for f in manifest.get("files", []) + manifest.get("contextFiles", [])}
    if path not in manifest_paths:
        return {"ok": False, "error": "FILE_NOT_IN_MANIFEST", "path": path}

    try:
        content = await store.read_text(app_slug, f"source/{path}")
    except FileNotFoundError:
        return {"ok": False, "error": "FILE_NOT_FOUND", "path": path}

    lines = content.splitlines()
    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or s + 79)
    # Clamp window
    if e - s + 1 > min(config.MAX_SOURCE_TOOL_LINES, 80):
        e = s + min(config.MAX_SOURCE_TOOL_LINES, 80) - 1
    if s > total or e < s:
        return {"ok": False, "error": "INVALID_LINE_RANGE", "totalLines": total}

    excerpt = "\n".join(lines[s - 1 : e])
    snippet = excerpt[:4000]
    actual_end = s + max(1, len(snippet.splitlines())) - 1
    return {
        "ok": True,
        "path": path,
        "startLine": s,
        "endLine": actual_end,
        "totalLines": total,
        "content": snippet,
        "truncated": actual_end < min(total, end_line or total) or len(excerpt) > len(snippet),
        "fileHasMoreLines": actual_end < total,
    }
