"""Behavioral coverage for focused pages and graph-first retrieval boundaries."""

from types import SimpleNamespace

import pytest

from coordinator.tools import documentation_tools as docs, graph_tools


class MemoryStore:
    def __init__(self, data):
        self.data = data
        self.reads = []

    async def read_json(self, app, path):
        self.reads.append((app, path))
        if path not in self.data:
            raise FileNotFoundError(path)
        return self.data[path]

    read_text = read_json

    async def write_json(self, app, path, value):
        self.data[path] = value

    write_text = write_json


@pytest.fixture
def context():
    return SimpleNamespace(state={"active_app": "selected-app", "page_mode": "repository"}, invocation_id="question-1")


def test_pages_follow_packages_and_keep_complete_deterministic_coverage():
    files = [{"path": p, "bytes": 10, "language": "python"} for p in
             ["src/controllers/payment.py", "src/services/charge.py", "tests/test_payment.py"]]
    groups = docs._build_file_chunks(files, 5)
    assert len(groups) == 3  # tiny packages should not collapse into a generic Src page
    assert groups == docs._build_file_chunks(list(reversed(files)), 5)
    assert docs._chunk_display_name(1, [files[0]["path"]]) == "Request handling"
    bounded = docs._build_file_chunks(files, 2)
    assert len(bounded) == 2
    assert sorted(f["path"] for group in bounded for f in group) == sorted(f["path"] for f in files)


@pytest.mark.asyncio
async def test_saved_title_comes_from_writer_and_keeps_stable_links(monkeypatch, context):
    store = MemoryStore({"source_manifest.json": {"files": [], "commitSha": "abc"}})
    monkeypatch.setattr(docs._rs, "get_store", lambda: store)
    saved = await docs.save_module_document("Module-1", "# Payment validation\nDetailed rules.", context)
    assert saved["title"] == "Payment validation"
    assert saved["slug"] == "module-1"
    assert store.data["documents.json"]["documents"][0]["title"] == "Payment validation"
    await docs.save_module_document("Module-1", "# Payment validation\nMore detail.", context,
                                    title="Payment settlement")
    assert len(store.data["documents.json"]["documents"]) == 1
    assert store.data["documents.json"]["documents"][0]["title"] == "Payment settlement"


@pytest.mark.asyncio
async def test_existing_page_titles_are_resolved_without_mutating_storage():
    store = MemoryStore({"docs/modules/module-1.md": "# Payment validation\nDetails"})
    old = [{"slug": "module-1", "title": "Module 1: Src", "kind": "module",
            "path": "docs/modules/module-1.md"}]
    result = await docs.document_navigation(store, "selected-app", old)
    assert result[0]["title"] == "Payment validation"
    assert result[0]["slug"] == "module-1"
    assert old[0]["title"] == "Module 1: Src"
    store.data["docs/modules/module-1.md"] = "# Module-1\nSee [src/services/payments.py:1-20]."
    result = await docs.document_navigation(store, "selected-app", old)
    assert result[0]["title"] == "Application services"


@pytest.mark.asyncio
async def test_discovery_and_context_use_the_same_module_boundaries(monkeypatch, context):
    paths = ["src/controllers/pay.py", "src/services/settle.py", "tests/test_pay.py"]
    store = MemoryStore({
        "source_manifest.json": {"files": [{"path": p, "bytes": 30, "language": "python"} for p in paths]},
        "graph.json": {"nodes": [{"id": p, "path": p, "kind": "module"} for p in paths], "edges": []},
        **{f"source/{p}": f"# {p}\npass" for p in paths},
    })
    monkeypatch.setattr(docs._rs, "get_store", lambda: store)
    modules = (await docs.list_documentation_modules(context))["modules"]
    assert len(modules) == 3
    for module, expected_path in zip(modules, paths):
        loaded = await docs.load_documentation_context(module["moduleId"], context)
        assert loaded["ok"]
        assert [s["path"] for s in loaded["sourceExcerpts"]] == [expected_path]
        assert loaded["suggestedTitle"] == module["displayName"]


@pytest.mark.asyncio
async def test_graph_summary_uses_only_selected_graph_and_manifest(monkeypatch, context):
    nodes = [{"id": "m", "kind": "module", "name": "payments", "language": "python",
              "path": "src/payments.py", "startLine": 1, "endLine": 40}]
    edges = []
    for i in range(35):
        nodes.append({"id": str(i), "kind": "external_symbol", "qualifiedName": f"library{i}"})
        edges.append({"sourceId": "m", "targetId": str(i), "kind": "IMPORTS", "path": "src/payments.py",
                      "startLine": i + 1, "endLine": i + 1, "resolution": "external"})
    store = MemoryStore({"graph.json": {"applicationSlug": "selected-app", "commitSha": "abc", "nodes": nodes, "edges": edges},
                         "source_manifest.json": {"contextFiles": [{"path": "pyproject.toml"}, {"path": "README.md"}]}})
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    result = (await graph_tools.retrieve_context("explain technology stack", "", context))["repository"]
    assert result["languages"] == ["python"]
    assert result["buildFiles"] == ["pyproject.toml"]
    assert len(result["imports"]) == 24 and result["truncated"]["imports"]
    assert result["imports"][0]["evidence"][0]["path"] == "src/payments.py"
    assert store.reads == [("selected-app", "symbol_index.json"), ("selected-app", "graph.json"), ("selected-app", "source_manifest.json")]
    assert context.state["active_app"] == "selected-app"
    assert context.state["page_mode"] == "repository"


@pytest.mark.asyncio
async def test_graph_missing_is_not_silently_replaced_with_documentation(monkeypatch, context):
    store = MemoryStore({})
    monkeypatch.setattr(graph_tools._rs, "get_store", lambda: store)
    assert (await graph_tools.retrieve_context("stack", "", context))["error"] == "RETRIEVAL_UNAVAILABLE"
    assert store.reads == [("selected-app", "symbol_index.json"), ("selected-app", "graph.json")]
