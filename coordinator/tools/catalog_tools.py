"""Catalog tools — list_applications, get_application_status, select_application. Phase 1."""

from __future__ import annotations

from datetime import UTC, datetime

from google.adk.tools.tool_context import ToolContext

from coordinator.schemas import is_valid_slug
from coordinator.tools import repo_store as _rs


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def list_applications(tool_context: ToolContext) -> dict:
    """Return all onboarded applications from the catalog.

    Rebuilds the catalog from metadata files if list_apps.json is missing or corrupt.
    """
    store = _rs.get_store()
    try:
        catalog = await store.read_json(None, "list_apps.json")
        if not isinstance(catalog.get("applications"), list):
            raise ValueError("corrupt catalog")
    except (FileNotFoundError, ValueError, KeyError):
        catalog = await _rebuild_catalog(store)

    return {"ok": True, "applications": catalog.get("applications", [])}


async def get_application_status(application_slug: str, tool_context: ToolContext) -> dict:
    """Return current status of a specific application.

    Validates the slug and reads metadata.json.
    """
    if not is_valid_slug(application_slug):
        return {"ok": False, "error": "INVALID_SLUG"}
    store = _rs.get_store()
    try:
        meta = await store.read_json(application_slug, "metadata.json")
    except FileNotFoundError:
        return {"ok": False, "error": "APPLICATION_NOT_FOUND"}

    try:
        job = await store.read_json(application_slug, "job.json")
    except FileNotFoundError:
        job = {}

    return {
        "ok": True,
        "applicationSlug": application_slug,
        "status": meta.get("status"),
        "displayName": meta.get("displayName"),
        "languages": meta.get("languages", []),
        "commitSha": meta.get("commitSha"),
        "branch": meta.get("resolvedBranch"),
        "statistics": meta.get("statistics", {}),
        "job": {
            "status": job.get("status"),
            "stage": job.get("stage"),
            "progressPercent": job.get("progressPercent"),
            "message": job.get("message", ""),
            "error": job.get("error"),
        }
        if job
        else None,
    }


async def select_application(application_slug: str, tool_context: ToolContext) -> dict:
    """Validate, verify the application is ready, and store it in session state.

    Sets session.state['active_app'] and session.state['active_commit_sha'].
    Never accepts a raw file path.
    """
    if not is_valid_slug(application_slug):
        return {"ok": False, "error": "INVALID_SLUG"}
    store = _rs.get_store()
    try:
        meta = await store.read_json(application_slug, "metadata.json")
    except FileNotFoundError:
        return {"ok": False, "error": "APPLICATION_NOT_FOUND"}

    if meta.get("status") != "ready":
        return {
            "ok": False,
            "error": "APPLICATION_NOT_READY",
            "status": meta.get("status"),
        }

    commit_sha = meta.get("commitSha", "")
    tool_context.state["active_app"] = application_slug
    tool_context.state["active_commit_sha"] = commit_sha

    return {
        "ok": True,
        "applicationSlug": application_slug,
        "displayName": meta.get("displayName"),
        "commitSha": commit_sha,
        "languages": meta.get("languages", []),
        "message": f"Selected application '{meta.get('displayName', application_slug)}'.",
    }


# ---------------------------------------------------------------------------
# Catalog rebuild helper
# ---------------------------------------------------------------------------


async def _rebuild_catalog(store=None) -> dict:
    """Read every metadata.json and rebuild list_apps.json."""
    if store is None:
        store = _rs.get_store()
    slugs = await store.list_application_slugs()
    apps = []
    for slug in sorted(slugs):
        try:
            meta = await store.read_json(slug, "metadata.json")
            apps.append(
                {
                    "applicationSlug": slug,
                    "displayName": meta.get("displayName", slug),
                    "repositoryUrl": meta.get("repositoryUrl", ""),
                    "branch": meta.get("resolvedBranch") or meta.get("requestedBranch", ""),
                    "commitSha": meta.get("commitSha", ""),
                    "status": meta.get("status", "unknown"),
                    "languages": meta.get("languages", []),
                    "updatedAt": meta.get("updatedAt", _now()),
                }
            )
        except FileNotFoundError:
            pass

    catalog = {
        "schemaVersion": "1.0",
        "updatedAt": _now(),
        "applications": apps,
    }
    try:
        await store.write_json(None, "list_apps.json", catalog)
    except Exception:
        pass  # best-effort; return what we have
    return catalog
