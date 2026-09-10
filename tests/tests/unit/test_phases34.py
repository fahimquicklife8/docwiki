"""Unit tests for Python and Java static analyzers — Phases 3–4."""

from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURE_PYTHON = Path(__file__).parent.parent / "fixtures" / "python_repo"
_FIXTURE_JAVA = Path(__file__).parent.parent / "fixtures" / "java_repo"


def _analyze_python(files: dict[str, str]):
    """Run both passes on a dict of {path: source_str}. Returns (st, rels)."""
    from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable
    from coordinator.tools.python_analyzer import PythonAnalyzer

    analyzer = PythonAnalyzer()
    st = SymbolTable()
    for path, src in files.items():
        analyzer.pass1_declarations(path, src.encode(), st)
    rels = RelationshipSet()
    for path, src in files.items():
        analyzer.pass2_relationships(path, src.encode(), st, rels)
    return st, rels


def _analyze_java(files: dict[str, str]):
    from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable
    from coordinator.tools.java_analyzer import JavaAnalyzer

    analyzer = JavaAnalyzer()
    st = SymbolTable()
    for path, src in files.items():
        analyzer.pass1_declarations(path, src.encode(), st)
    rels = RelationshipSet()
    for path, src in files.items():
        analyzer.pass2_relationships(path, src.encode(), st, rels)
    return st, rels


# ---------------------------------------------------------------------------
# Python analyzer
# ---------------------------------------------------------------------------


def test_python_extracts_class():
    st, _ = _analyze_python({"mymodule.py": "class Foo:\n    pass\n"})
    class_nodes = [n for n in st.nodes.values() if n["kind"] == "class"]
    assert any(n["name"] == "Foo" for n in class_nodes)


def test_python_extracts_method():
    src = "class Foo:\n    def bar(self, x):\n        pass\n"
    st, _ = _analyze_python({"mymodule.py": src})
    method_nodes = [n for n in st.nodes.values() if n["kind"] == "method"]
    assert any(n["name"] == "bar" for n in method_nodes)


def test_python_extracts_function():
    src = "def my_func(a, b):\n    return a + b\n"
    st, _ = _analyze_python({"mymod.py": src})
    func_nodes = [n for n in st.nodes.values() if n["kind"] == "function"]
    assert any(n["name"] == "my_func" for n in func_nodes)


def test_python_self_call_is_exact():
    src = "class Svc:\n    def a(self):\n        return self.b()\n    def b(self):\n        pass\n"
    st, rels = _analyze_python({"svc.py": src})
    calls = [e for e in rels.edges if e["kind"] == "CALLS"]
    exact_calls = [e for e in calls if e["resolution"] == "exact"]
    assert len(exact_calls) >= 1


def test_python_unresolved_external():
    src = "import external_lib as ext\ndef foo():\n    ext.do_something()\n"
    st, rels = _analyze_python({"mod.py": src})
    ext_calls = [e for e in rels.edges if e["kind"] == "CALLS" and e["resolution"] == "external"]
    assert len(ext_calls) >= 1


def test_python_fixture_parses():
    """Fixture repo parses without error."""
    files = {}
    for p in _FIXTURE_PYTHON.rglob("*.py"):
        files[p.relative_to(_FIXTURE_PYTHON).as_posix()] = p.read_text()
    st, rels = _analyze_python(files)
    assert len(st.nodes) > 0
    assert len(rels.edges) > 0


def test_python_fixture_contains_edges():
    files = {}
    for p in _FIXTURE_PYTHON.rglob("*.py"):
        files[p.relative_to(_FIXTURE_PYTHON).as_posix()] = p.read_text()
    _, rels = _analyze_python(files)
    contains = [e for e in rels.edges if e["kind"] == "CONTAINS"]
    assert len(contains) > 0


def test_python_node_ids_deterministic():
    src = "class Foo:\n    def bar(self):\n        pass\n"
    st1, _ = _analyze_python({"m.py": src})
    st2, _ = _analyze_python({"m.py": src})
    assert set(st1.nodes.keys()) == set(st2.nodes.keys())


# ---------------------------------------------------------------------------
# Java analyzer
# ---------------------------------------------------------------------------


def test_java_extracts_class():
    src = "package com.ex;\npublic class Foo {}\n"
    st, _ = _analyze_java({"Foo.java": src})
    classes = [n for n in st.nodes.values() if n["kind"] == "class"]
    assert any(n["name"] == "Foo" for n in classes)


def test_java_extracts_interface():
    src = "package com.ex;\npublic interface Chargeable { void charge(); }\n"
    st, _ = _analyze_java({"Chargeable.java": src})
    ifaces = [n for n in st.nodes.values() if n["kind"] == "interface"]
    assert any(n["name"] == "Chargeable" for n in ifaces)


def test_java_extracts_method():
    src = "package com.ex;\npublic class Svc { public void doIt() {} }\n"
    st, _ = _analyze_java({"Svc.java": src})
    methods = [n for n in st.nodes.values() if n["kind"] == "method"]
    assert any(n["name"] == "doIt" for n in methods)


def test_java_this_call_exact():
    src = (
        "package com.ex;\n"
        "public class Svc {\n"
        "  public void a() { this.b(); }\n"
        "  public void b() {}\n"
        "}\n"
    )
    st, rels = _analyze_java({"Svc.java": src})
    calls = [e for e in rels.edges if e["kind"] == "CALLS"]
    assert any(e["resolution"] in ("exact", "heuristic") for e in calls)


def test_java_fixture_parses():
    files = {}
    for p in _FIXTURE_JAVA.rglob("*.java"):
        files[p.relative_to(_FIXTURE_JAVA).as_posix()] = p.read_text()
    st, rels = _analyze_java(files)
    assert len(st.nodes) > 0


def test_java_overloaded_methods_both_present():
    src = (
        "package com.ex;\n"
        "public class Repo {\n"
        "  public String find(String id) { return id; }\n"
        "  public String find(String id, boolean flag) { return id; }\n"
        "}\n"
    )
    st, _ = _analyze_java({"Repo.java": src})
    find_methods = [n for n in st.nodes.values() if n.get("name") == "find"]
    assert len(find_methods) >= 2


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def test_graph_builder_output_valid():
    from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable
    from coordinator.tools.graph_builder import build_graph, validate_graph

    src = "class Foo:\n    def bar(self):\n        pass\n"
    from coordinator.tools.python_analyzer import PythonAnalyzer

    analyzer = PythonAnalyzer()
    st = SymbolTable()
    analyzer.pass1_declarations("mod.py", src.encode(), st)
    rels = RelationshipSet()
    analyzer.pass2_relationships("mod.py", src.encode(), st, rels)

    graph = build_graph("test-app", "abc123", st, rels)
    validate_graph(graph, 15000, 50000)  # should not raise


def test_symbol_index_built():
    from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable
    from coordinator.tools.graph_builder import build_graph, build_symbol_index
    from coordinator.tools.python_analyzer import PythonAnalyzer

    src = "class MyService:\n    def process(self):\n        pass\n"
    analyzer = PythonAnalyzer()
    st = SymbolTable()
    analyzer.pass1_declarations("svc.py", src.encode(), st)
    rels = RelationshipSet()
    analyzer.pass2_relationships("svc.py", src.encode(), st, rels)

    graph = build_graph("app", "sha", st, rels)
    idx = build_symbol_index("app", "sha", graph)
    symbols = idx["symbols"]
    names = {s["name"] for s in symbols}
    assert "MyService" in names or "process" in names


def test_graph_validation_rejects_bad_node_kind():
    from coordinator.tools.graph_builder import validate_graph

    graph = {
        "nodes": [
            {
                "id": "x",
                "kind": "not_a_kind",
                "name": "x",
                "qualifiedName": "x",
                "language": "python",
                "path": "x.py",
                "startLine": 1,
                "endLine": 1,
                "signature": "",
                "parentId": None,
                "attributes": {},
            }
        ],
        "edges": [],
    }
    with pytest.raises(ValueError):
        validate_graph(graph, 15000, 50000)
