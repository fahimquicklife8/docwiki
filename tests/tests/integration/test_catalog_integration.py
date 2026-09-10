"""Integration tests for catalog tools and LocalRepoStore — Phases 1 & 6."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ["DOCWIKI_STORAGE_MODE"] = "local"


def _make_ctx(state: dict | None = None):
    ctx = MagicMock()
    s = {} if state is None else state
    ctx.state = s
    return ctx


# ---------------------------------------------------------------------------
# Catalog tools with real LocalRepoStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_applications_empty(tmp_repos):
    from coordinator.tools.catalog_tools import list_applications
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    # Patch get_store
    import coordinator.tools.catalog_tools as ct
    import coordinator.tools.repo_store as rs

    orig = rs.get_store
    rs.get_store = lambda: store
    ct_orig = ct  # noqa

    try:
        result = await list_applications(_make_ctx())
        assert result["ok"] is True
        assert result["applications"] == []
    finally:
        rs.get_store = orig


@pytest.mark.asyncio
async def test_list_applications_rebuilds_from_metadata(tmp_repos):
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    # Write a metadata.json without a list_apps.json
    await store.write_json(
        "my-app",
        "metadata.json",
        {
            "schemaVersion": "1.0",
            "applicationSlug": "my-app",
            "displayName": "My App",
            "repositoryOwner": "Org",
            "repositoryName": "repo",
            "repositoryUrl": "https://github.com/Org/repo",
            "status": "ready",
            "languages": ["python"],
            "resolvedBranch": "main",
            "commitSha": "abc",
            "statistics": {},
            "createdAt": "2026-07-22T00:00:00Z",
            "updatedAt": "2026-07-22T00:00:00Z",
        },
    )

    orig = rs.get_store
    rs.get_store = lambda: store
    try:
        from coordinator.tools.catalog_tools import list_applications

        result = await list_applications(_make_ctx())
        assert result["ok"] is True
        slugs = [a["applicationSlug"] for a in result["applications"]]
        assert "my-app" in slugs
    finally:
        rs.get_store = orig


@pytest.mark.asyncio
async def test_select_application_sets_session_state(tmp_repos):
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_json(
        "app-one",
        "metadata.json",
        {
            "schemaVersion": "1.0",
            "applicationSlug": "app-one",
            "displayName": "App One",
            "repositoryOwner": "Org",
            "repositoryName": "AppOne",
            "repositoryUrl": "https://github.com/Org/AppOne",
            "status": "ready",
            "languages": [],
            "commitSha": "sha1",
            "statistics": {},
            "createdAt": "2026-07-22T00:00:00Z",
            "updatedAt": "2026-07-22T00:00:00Z",
        },
    )

    state: dict = {}
    ctx = _make_ctx(state)
    orig = rs.get_store
    rs.get_store = lambda: store
    try:
        from coordinator.tools.catalog_tools import select_application

        result = await select_application("app-one", ctx)
        assert result["ok"] is True
        assert state["active_app"] == "app-one"
        assert state["active_commit_sha"] == "sha1"
    finally:
        rs.get_store = orig


@pytest.mark.asyncio
async def test_select_application_rejects_not_ready(tmp_repos):
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_json(
        "app-two",
        "metadata.json",
        {
            "schemaVersion": "1.0",
            "applicationSlug": "app-two",
            "displayName": "App Two",
            "repositoryOwner": "O",
            "repositoryName": "R",
            "repositoryUrl": "https://github.com/O/R",
            "status": "processing",
            "languages": [],
            "statistics": {},
            "createdAt": "2026-07-22T00:00:00Z",
            "updatedAt": "2026-07-22T00:00:00Z",
        },
    )
    orig = rs.get_store
    rs.get_store = lambda: store
    try:
        from coordinator.tools.catalog_tools import select_application

        result = await select_application("app-two", _make_ctx())
        assert result["ok"] is False
        assert result["error"] == "APPLICATION_NOT_READY"
    finally:
        rs.get_store = orig


@pytest.mark.asyncio
async def test_switch_active_app_isolates_tools(tmp_repos):
    """Switching active_app changes which app the tools access."""
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    for slug, name in [("app-a", "App A"), ("app-b", "App B")]:
        await store.write_json(
            slug,
            "metadata.json",
            {
                "schemaVersion": "1.0",
                "applicationSlug": slug,
                "displayName": name,
                "repositoryOwner": "O",
                "repositoryName": slug,
                "repositoryUrl": f"https://github.com/O/{slug}",
                "status": "ready",
                "languages": [],
                "commitSha": slug + "-sha",
                "statistics": {},
                "createdAt": "2026-07-22T00:00:00Z",
                "updatedAt": "2026-07-22T00:00:00Z",
            },
        )
        await store.write_json(
            slug,
            "symbol_index.json",
            {
                "schemaVersion": "1.0",
                "applicationSlug": slug,
                "commitSha": slug + "-sha",
                "symbols": [],
            },
        )

    state: dict = {}
    ctx = _make_ctx(state)
    orig = rs.get_store
    rs.get_store = lambda: store
    try:
        from coordinator.tools.catalog_tools import select_application
        from coordinator.tools.graph_tools import search_symbols

        await select_application("app-a", ctx)
        assert state["active_app"] == "app-a"

        await select_application("app-b", ctx)
        assert state["active_app"] == "app-b"

        # search_symbols now operates on app-b
        result = await search_symbols("anything", None, 10, ctx)
        assert result["ok"] is True
    finally:
        rs.get_store = orig


# ---------------------------------------------------------------------------
# Source read tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_source_validates_path(tmp_repos):
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_json(
        "app-x",
        "source_manifest.json",
        {
            "schemaVersion": "1.0",
            "applicationSlug": "app-x",
            "commitSha": "s",
            "files": [
                {"path": "foo.py", "language": "python", "bytes": 5, "sha256": "x", "lineCount": 1}
            ],
        },
    )
    await store.write_text("app-x", "source/foo.py", "x = 1\n")

    state = {"active_app": "app-x"}
    ctx = _make_ctx(state)
    orig = rs.get_store
    rs.get_store = lambda: store
    try:
        from coordinator.tools.source_tools import read_source

        result = await read_source("foo.py", None, None, ctx)
        assert result["ok"] is True
        assert "x = 1" in result["content"]

        bad = await read_source("not_in_manifest.py", None, None, ctx)
        assert bad["ok"] is False
        assert bad["error"] == "FILE_NOT_IN_MANIFEST"
    finally:
        rs.get_store = orig


# ---------------------------------------------------------------------------
# Cold-start simulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_reads_from_local_store(tmp_repos):
    """After recreating the store with empty memory, artifacts are still accessible."""
    import coordinator.tools.repo_store as rs
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_json(
        "cold-app",
        "metadata.json",
        {
            "schemaVersion": "1.0",
            "applicationSlug": "cold-app",
            "displayName": "Cold",
            "repositoryOwner": "O",
            "repositoryName": "R",
            "repositoryUrl": "https://github.com/O/R",
            "status": "ready",
            "languages": ["python"],
            "commitSha": "cold-sha",
            "statistics": {},
            "createdAt": "2026-07-22T00:00:00Z",
            "updatedAt": "2026-07-22T00:00:00Z",
        },
    )

    # Simulate cold start: new store instance with same root
    store2 = LocalRepoStore(root=tmp_repos)
    orig = rs.get_store
    rs.get_store = lambda: store2
    try:
        from coordinator.tools.catalog_tools import list_applications

        result = await list_applications(_make_ctx())
        slugs = [a["applicationSlug"] for a in result["applications"]]
        assert "cold-app" in slugs
    finally:
        rs.get_store = orig
