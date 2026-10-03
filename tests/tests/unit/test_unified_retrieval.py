"""Retrieval cost stays bounded as repository size and requested tool calls grow."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from coordinator.tools import graph_tools
from coordinator.tools.graph_builder import build_symbol_index


class Store:
    def __init__(self, data, readonly=False):
        self.data, self.reads, self.readonly = data, [], readonly

    async def read_json(self, app, path):
        self.reads.append((app, path))
        await asyncio.sleep(0)  # Exercise concurrent tool calls at the first IO boundary.
        if path not in self.data:
            raise FileNotFoundError(path)
        return self.data[path]

    read_text = read_json

    async def write_json(self, app, path, data):
        if self.readonly:
            raise PermissionError(path)
        self.data[path] = data


def context(invocation="first", app="selected-app", commit="abc"):
    return SimpleNamespace(invocation_id=invocation, state={
        "active_app": app, "active_commit_sha": commit, "page_mode": "repository"})


def repository(size=3000):
    nodes, edges, files = [], [], []
    for i in range(size):
        path = f"projects/project{i}/payment.py"
        files.append({"path": path, "language": "python"})
        nodes.append({"id": f"module{i}", "kind": "module", "name": f"payment{i}", "language": "python",
                      "path": path, "startLine": 1, "endLine": 200})
        edges.append({"sourceId": f"module{i}", "targetId": "fastapi", "kind": "IMPORTS",
                      "resolution": "external", "path": path, "startLine": 1, "endLine": 1})
    nodes.append({"id": "fastapi", "kind": "external_symbol", "qualifiedName": "fastapi.FastAPI", "language": "python"})
    graph = {"applicationSlug": "selected-app", "commitSha": "abc", "nodes": nodes, "edges": edges}
    manifest = {"files": files, "contextFiles": [{"path": "pyproject.toml"}]}
    return {"graph.json": graph, "source_manifest.json": manifest}


@pytest.mark.asyncio
async def test_3000_files_one_index_read_zero_source_reads(monkeypatch):
    data = repository()
    data["symbol_index.json"] = build_symbol_index("selected-app", "abc", data["graph.json"], data["source_manifest.json"])
    store = Store(data)
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    ctx = context()
    result = await graph_tools.retrieve_context("explain the technology stack of this application", "", ctx)
    assert result["ok"]
    assert result["repository"]["imports"][0]["name"] == "fastapi"
    assert result["repository"]["imports"][0]["fileCount"] == 3000
    assert result["repository"]["coverage"]["sourceFiles"] == 3000
    assert store.reads == [("selected-app", "symbol_index.json")]
    assert len(json.dumps(result, ensure_ascii=False)) <= graph_tools.MAX_RESPONSE_CHARS
    assert len(json.dumps(ctx.state)) < 10000  # Never put the full index in session/event state.


@pytest.mark.asyncio
async def test_fourteen_followups_cannot_read_fourteen_files(monkeypatch):
    data = repository(30)
    data["source/pyproject.toml"] = "[project]\ndependencies = ['fastapi==1.0']"
    data.update({f"source/{f['path']}": "import fastapi\n" * 200 for f in data["source_manifest.json"]["files"]})
    store = Store(data)
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    ctx = context()
    first = await graph_tools.retrieve_context("How does payment1 use fastapi?", "", ctx)
    assert first["ok"] and first["graph"]["matchedSymbols"]
    fallback = await graph_tools.retrieve_context("fastapi", "Exact dependency version in pyproject.toml", ctx)
    assert fallback["ok"] and fallback["retrievalComplete"]
    assert len(fallback["excerpts"]) <= 2
    assert fallback["excerpts"][0]["path"] == "pyproject.toml"
    for _ in range(13):
        result = await graph_tools.retrieve_context("another file", "more evidence", ctx)
        assert result["error"] == "RETRIEVAL_COMPLETE"
    assert len([p for _, p in store.reads if p.startswith("source/")]) == 2
    assert all(len(e["content"]) <= 4000 and e["endLine"] - e["startLine"] < 80 for e in fallback["excerpts"])


@pytest.mark.asyncio
async def test_parallel_calls_do_not_multiply_io(monkeypatch):
    store = Store(repository(10))
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    ctx = context()
    results = await asyncio.gather(*(graph_tools.retrieve_context("payment1", "", ctx) for _ in range(15)))
    assert sum(r["ok"] for r in results) == 1
    assert sum(r.get("error") == "RETRIEVAL_IN_PROGRESS" for r in results) == 14
    assert len(store.reads) == 3  # missing index, graph, manifest, no source reads


@pytest.mark.asyncio
async def test_missing_invalid_and_stale_cache_fallback_without_source(monkeypatch):
    for cache in (None, {"schemaVersion": "bad"}, {"schemaVersion": "2.0", "commitSha": "old"}):
        data = repository(3)
        if cache is not None:
            data["symbol_index.json"] = cache
        store = Store(data, readonly=True)
        monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
        result = await graph_tools.retrieve_context("stack", "", context())
        assert result["ok"]
        assert not any(p.startswith("source/") for _, p in store.reads)


@pytest.mark.asyncio
async def test_stale_graph_and_missing_source_report_gaps(monkeypatch):
    store = Store(repository(3))
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    assert not (await graph_tools.retrieve_context("stack", "", context(commit="new")))["ok"]
    ctx = context()
    assert (await graph_tools.retrieve_context("payment1", "", ctx))["ok"]
    fallback = await graph_tools.retrieve_context("payment1", "validation branch", ctx)
    assert not fallback["ok"] and fallback["gaps"] and fallback["retrievalComplete"]


@pytest.mark.asyncio
async def test_new_question_and_application_have_independent_budgets(monkeypatch):
    store = Store(repository(3))
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    ctx = context()
    await graph_tools.retrieve_context("payment1", "", ctx)
    await graph_tools.retrieve_context("payment2", "", ctx)
    ctx.invocation_id = "second"
    assert (await graph_tools.retrieve_context("stack", "", ctx))["ok"]
    ctx.state["active_app"] = "different-app"
    assert graph_tools._turn(ctx)["calls"] == 0
    assert ctx.state["page_mode"] == "repository"


@pytest.mark.asyncio
async def test_recovery_after_missing_snapshot_still_returns_overview(monkeypatch):
    store = Store({})
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    ctx = context()
    assert not (await graph_tools.retrieve_context("stack", "", ctx))["ok"]
    store.data.update(repository(3))
    recovered = await graph_tools.retrieve_context("stack", "", ctx)
    assert recovered["ok"] and recovered["repository"]["coverage"]["sourceFiles"] == 3


def test_response_budget_keeps_graph_endpoints_consistent():
    data = repository(100)
    index = build_symbol_index("selected-app", "abc", data["graph.json"], data["source_manifest.json"])
    graph = graph_tools._graph_evidence(index, "payment1 fastapi")
    for node in graph["nodes"]:
        node["signature"] = "x" * 12000
    result = graph_tools._fit_response({"ok": True, "graph": graph, "repository": index["summary"]})
    assert len(json.dumps(result, ensure_ascii=False)) <= graph_tools.MAX_RESPONSE_CHARS
    ids = {n["id"] for n in result["graph"]["nodes"]}
    assert all(e["sourceId"] in ids and e["targetId"] in ids for e in result["graph"]["edges"])
