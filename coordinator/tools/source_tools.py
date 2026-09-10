"""Source read/search tools — read_source, search_source. Phase 6."""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.tools import repo_store as _rs


def _get_active_app(tool_context: ToolContext) -> str | None:
    """Return the active application slug from session state, or None."""
    return tool_context.state.get("active_app")


async def read_source(
    path: str,
    start_line: int | None,
    end_line: int | None,
    tool_context: ToolContext,
) -> dict:
    """Read source lines for the active application.

    Validates path against source_manifest.json and clamps line range.
    """
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    manifest_paths = {f["path"] for f in manifest.get("files", [])}
    if path not in manifest_paths:
        return {"ok": False, "error": "FILE_NOT_IN_MANIFEST", "path": path}

    try:
        content = await store.read_text(app_slug, f"source/{path}")
    except FileNotFoundError:
        return {"ok": False, "error": "FILE_NOT_FOUND", "path": path}

    lines = content.splitlines()
    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or total)
    # Clamp window
    if e - s + 1 > config.MAX_SOURCE_TOOL_LINES:
        e = s + config.MAX_SOURCE_TOOL_LINES - 1

    snippet = "\n".join(lines[s - 1 : e])
    return {
        "ok": True,
        "path": path,
        "startLine": s,
        "endLine": e,
        "totalLines": total,
        "content": snippet,
    }


async def search_source(
    query: str,
    path_prefix: str | None,
    limit: int,
    tool_context: ToolContext,
) -> dict:
    """Lexically search manifest-listed source files of the active application."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    cap = min(max(1, limit), 50)
    ql = query.lower()
    matches: list[dict] = []
    files_searched = 0

    for entry in manifest.get("files", []):
        fp = entry["path"]
        if path_prefix and not fp.startswith(path_prefix):
            continue
        if files_searched >= 200:  # cap files searched
            break
        files_searched += 1

        try:
            content = await store.read_text(app_slug, f"source/{fp}")
        except FileNotFoundError:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            if ql in line.lower():
                matches.append(
                    {
                        "path": fp,
                        "line": lineno,
                        "content": line.rstrip(),
                    }
                )
                if len(matches) >= cap * 5:
                    break

    return {"ok": True, "query": query, "matches": matches[:cap]}
