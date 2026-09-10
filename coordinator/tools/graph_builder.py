"""Graph builder and validator. Phase 3."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from coordinator.schemas import EDGE_KINDS, NODE_KINDS, RESOLUTION_VALUES
from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable

_VALID_PATH_RE = re.compile(r"^[^/\\]")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_graph(
    app_slug: str,
    commit_sha: str,
    symbol_table: SymbolTable,
    relationships: RelationshipSet,
) -> dict[str, Any]:
    """Assemble a graph.json dict from the symbol table and relationships."""
    nodes = list(symbol_table.nodes.values())
    edges = relationships.edges

    stats = {
        "nodes": len(nodes),
        "edges": len(edges),
        "externalNodes": sum(1 for n in nodes if n.get("kind") == "external_symbol"),
    }

    return {
        "schemaVersion": "1.0",
        "applicationSlug": app_slug,
        "commitSha": commit_sha,
        "generatedAt": _now(),
        "nodes": nodes,
        "edges": edges,
        "statistics": stats,
    }


def validate_graph(graph: dict[str, Any], max_nodes: int, max_edges: int) -> None:
    """Validate graph structure. Raises ValueError on violation."""
    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])

    if len(nodes) > max_nodes:
        raise ValueError(f"GRAPH_VALIDATION_FAILED: too many nodes ({len(nodes)} > {max_nodes})")
    if len(edges) > max_edges:
        raise ValueError(f"GRAPH_VALIDATION_FAILED: too many edges ({len(edges)} > {max_edges})")

    node_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not nid:
            raise ValueError("GRAPH_VALIDATION_FAILED: node missing id")
        if nid in node_ids:
            raise ValueError(f"GRAPH_VALIDATION_FAILED: duplicate node id {nid!r}")
        node_ids.add(nid)
        kind = n.get("kind", "")
        if kind not in NODE_KINDS:
            raise ValueError(f"GRAPH_VALIDATION_FAILED: invalid node kind {kind!r}")
        if n.get("startLine", 1) < 0 or n.get("endLine", 1) < n.get("startLine", 1):
            raise ValueError(f"GRAPH_VALIDATION_FAILED: bad line range for {nid}")

    edge_ids: set[str] = set()
    for e in edges:
        eid = e.get("id")
        if not eid:
            raise ValueError("GRAPH_VALIDATION_FAILED: edge missing id")
        if eid in edge_ids:
            # Duplicate edge IDs are allowed (same semantic edge, different sites)
            pass
        edge_ids.add(eid)
        if e.get("kind", "") not in EDGE_KINDS:
            raise ValueError(f"GRAPH_VALIDATION_FAILED: invalid edge kind {e.get('kind')!r}")
        if e.get("resolution", "") not in RESOLUTION_VALUES:
            raise ValueError(f"GRAPH_VALIDATION_FAILED: invalid resolution {e.get('resolution')!r}")
        conf = e.get("confidence", 1.0)
        if not (0.0 <= conf <= 1.0):
            raise ValueError(f"GRAPH_VALIDATION_FAILED: confidence out of range for {eid}")
        # Endpoints must exist in node_ids
        if e.get("sourceId") not in node_ids:
            raise ValueError(
                f"GRAPH_VALIDATION_FAILED: edge {eid} sourceId not found: {e.get('sourceId')}"
            )
        if e.get("targetId") not in node_ids:
            raise ValueError(
                f"GRAPH_VALIDATION_FAILED: edge {eid} targetId not found: {e.get('targetId')}"
            )


def build_symbol_index(app_slug: str, commit_sha: str, graph: dict[str, Any]) -> dict[str, Any]:
    """Build symbol_index.json from graph nodes."""
    symbols = []
    for n in graph.get("nodes", []):
        kind = n.get("kind", "")
        if kind in ("file", "external_symbol"):
            continue
        name = n.get("name", "")
        qname = n.get("qualifiedName", "")
        tokens = _tokenize(name) + _tokenize(qname)
        symbols.append(
            {
                "id": n["id"],
                "name": name,
                "qualifiedName": qname,
                "kind": kind,
                "path": n.get("path", ""),
                "startLine": n.get("startLine", 0),
                "endLine": n.get("endLine", 0),
                "signature": n.get("signature", ""),
                "tokens": sorted(set(tokens)),
            }
        )
    return {
        "schemaVersion": "1.0",
        "applicationSlug": app_slug,
        "commitSha": commit_sha,
        "symbols": symbols,
    }


def _tokenize(text: str) -> list[str]:
    """Split a qualified name or identifier into normalized search tokens."""
    # Split on non-alphanumeric
    parts = re.split(r"[^a-zA-Z0-9]+", text)
    tokens = []
    for part in parts:
        if not part:
            continue
        lpart = part.lower()
        tokens.append(lpart)
        # CamelCase split
        subs = re.sub(r"([A-Z][a-z]+)", r" \1", re.sub(r"([A-Z]+)", r" \1", part))
        for sub in subs.split():
            tokens.append(sub.lower())
    return tokens
