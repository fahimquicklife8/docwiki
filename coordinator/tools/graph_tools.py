"""Graph query tools — search_symbols, get_symbol, get_graph_neighbors. Phase 6."""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.tools import repo_store as _rs
from coordinator.tools.graph_builder import _tokenize
from coordinator.tools.source_tools import _get_active_app


async def search_symbols(
    query: str,
    kinds: list[str] | None,
    limit: int,
    tool_context: ToolContext,
) -> dict:
    """Lexically search symbol_index.json for the active application."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        index = await store.read_json(app_slug, "symbol_index.json")
    except FileNotFoundError:
        return {"ok": False, "error": "SYMBOL_INDEX_NOT_FOUND"}

    query_tokens = set(_tokenize(query.lower()))
    if not query_tokens:
        return {"ok": True, "symbols": []}

    results = []
    for sym in index.get("symbols", []):
        if kinds and sym.get("kind") not in kinds:
            continue
        name = sym.get("name", "")
        qname = sym.get("qualifiedName", "")
        sym_tokens = set(sym.get("tokens", []))
        # Scoring: exact name → 100, qname prefix → 80, token prefix → 50, substring → 20
        score = 0
        if name.lower() == query.lower():
            score = 100
        elif qname.lower().endswith("." + query.lower()) or qname.lower() == query.lower():
            score = 90
        elif name.lower().startswith(query.lower()):
            score = 80
        elif any(t.startswith(qt) for qt in query_tokens for t in sym_tokens):
            score = 50
        elif query.lower() in name.lower() or query.lower() in qname.lower():
            score = 20
        elif query_tokens & sym_tokens:
            score = 10
        if score > 0:
            results.append((score, sym))

    results.sort(key=lambda x: -x[0])
    cap = min(max(1, limit), 100)
    return {"ok": True, "symbols": [s for _, s in results[:cap]]}


async def get_symbol(symbol_id: str, tool_context: ToolContext) -> dict:
    """Return full details for a symbol node by ID."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        graph = await store.read_json(app_slug, "graph.json")
    except FileNotFoundError:
        return {"ok": False, "error": "GRAPH_NOT_FOUND"}

    for n in graph.get("nodes", []):
        if n.get("id") == symbol_id:
            return {"ok": True, "symbol": n}
    return {"ok": False, "error": "SYMBOL_NOT_FOUND"}


async def get_graph_neighbors(
    symbol_id: str,
    direction: str,
    edge_kinds: list[str] | None,
    depth: int,
    limit: int,
    tool_context: ToolContext,
) -> dict:
    """Return neighbouring nodes in the call graph for the active application."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        graph = await store.read_json(app_slug, "graph.json")
    except FileNotFoundError:
        return {"ok": False, "error": "GRAPH_NOT_FOUND"}

    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    if symbol_id not in nodes_by_id:
        return {"ok": False, "error": "SYMBOL_NOT_FOUND"}

    edges = graph.get("edges", [])
    cap_depth = min(max(1, depth), 2)
    cap_limit = min(max(1, limit), config.MAX_GRAPH_NEIGHBOR_NODES)
    allowed_kinds = set(edge_kinds) if edge_kinds else None

    visited_nodes: set[str] = {symbol_id}
    frontier: set[str] = {symbol_id}
    result_edges: list[dict] = []
    result_nodes: list[dict] = []

    for _ in range(cap_depth):
        next_frontier: set[str] = set()
        for edge in edges:
            if allowed_kinds and edge.get("kind") not in allowed_kinds:
                continue
            src, tgt = edge.get("sourceId"), edge.get("targetId")
            if direction in ("outgoing", "both") and src in frontier and tgt not in visited_nodes:
                result_edges.append(edge)
                next_frontier.add(tgt)
                visited_nodes.add(tgt)
            if direction in ("incoming", "both") and tgt in frontier and src not in visited_nodes:
                result_edges.append(edge)
                next_frontier.add(src)
                visited_nodes.add(src)
        frontier = next_frontier
        for nid in frontier:
            n = nodes_by_id.get(nid)
            if n:
                result_nodes.append(n)
        if len(result_nodes) >= cap_limit:
            break

    return {
        "ok": True,
        "originId": symbol_id,
        "nodes": result_nodes[:cap_limit],
        "edges": result_edges,
    }
