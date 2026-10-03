"""Preserve converging, parallel, and cycle relationships in unified retrieval."""

from coordinator.tools.graph_tools import _graph_evidence
from coordinator.tools.graph_builder import build_symbol_index


def test_neighbors_preserve_converging_parallel_and_cycle_edges():
    edges = [
        {"sourceId": src, "targetId": tgt, "kind": kind}
        for src, tgt, kind in [
            ("a", "b", "CALLS"), ("a", "c", "CALLS"),
            ("b", "c", "CALLS"), ("b", "a", "CALLS"),
            ("a", "a", "CALLS"), ("a", "b", "IMPORTS"),
        ]
    ]
    index = build_symbol_index("app", "sha", {
        "nodes": [{"id": n, "name": n, "kind": "function"} for n in "abc"],
        "edges": edges,
    }, {})
    result = _graph_evidence(index, "a b c")
    assert len(result["edges"]) == len(edges)
    assert all(edge in result["edges"] for edge in edges)
    assert {n["id"] for n in result["nodes"]} == set("abc")
