"""One repository retrieval interface: ranked graph evidence, one bounded fallback."""

from __future__ import annotations

import json
import math
from collections import deque

from google.adk.tools.tool_context import ToolContext
from google.api_core.exceptions import GoogleAPIError

from coordinator.tools import repo_store as _rs
from coordinator.tools.graph_builder import INDEX_VERSION, _tokenize, build_symbol_index
from coordinator.tools.source_tools import _read_source_excerpt

MAX_CALLS = 6
MAX_RESPONSE_CHARS = 18000
MAX_TURN_CHARS = 36000
MAX_SOURCE_CHARS = 8000
MAX_GRAPH_NODES = 36
MAX_GRAPH_EDGES = 64


def _turn(context):
    key = f"temp:retrieval:{context.invocation_id}:{context.state.get('active_app')}:{context.state.get('active_commit_sha')}"
    return context.state.setdefault(key, {"calls": 0, "busy": False, "chars": 0, "sourceChars": 0, "sourceWindows": [], "candidates": [], "overviewSent": False, "requests": []})


async def _load_index(store, app, commit):
    try:
        index = await store.read_json(app, "symbol_index.json")
        if (isinstance(index, dict) and index.get("schemaVersion") == INDEX_VERSION
                and index.get("applicationSlug") == app and (not commit or index.get("commitSha") == commit)
                and all(isinstance(index.get(k), dict) for k in ("nodes", "terms", "adjacency", "summary", "children", "references"))
                and isinstance(index.get("edges"), list)):
            return index
    except (OSError, ValueError, GoogleAPIError):
        pass
    graph = await store.read_json(app, "graph.json")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError("Graph is unavailable or invalid")
    if commit and graph.get("commitSha") != commit:
        raise ValueError("Graph does not match the selected commit")
    if graph.get("applicationSlug") not in (None, app):
        raise ValueError("Graph does not match the selected application")
    try:
        manifest = await store.read_json(app, "source_manifest.json")
    except (OSError, ValueError, GoogleAPIError):
        manifest = {}
    index = build_symbol_index(app, graph.get("commitSha"), graph, manifest)
    try:
        await store.write_json(app, "symbol_index.json", index)
    except (OSError, ValueError, GoogleAPIError):
        pass  # Read-only storage still permits an in-memory retrieval result.
    return index


# Ordinary lexical stopwords, not question/scenario routing rules.
_STOPWORDS = set("a an the this that these those is are was were be been being do does did what which who where when how why of to for from in on at and or with without it its i we you me my our your can could would should please explain show tell describe possible program application repository codebase diagram mermaid".split())


def _rank(index, query):
    terms = set(_tokenize(query)) - _STOPWORDS
    scores = {}
    for term in terms:
        matches = index["terms"].get(term, [])
        idf = math.log1p(len(index["nodes"]) / max(1, len(matches)))
        for node_id in matches:
            node = index["nodes"][node_id]
            name_terms = set(_tokenize(node.get("name", "")))
            qualified_terms = set(_tokenize(node.get("qualifiedName", "")))
            weight = 5 if term in name_terms else 2 if term in qualified_terms else 0.2
            if node.get("kind") in {"file", "package"}:
                weight *= 0.25
            scores[node_id] = scores.get(node_id, 0) + idf * weight
    ranked = sorted(scores, key=lambda n: (-scores[n], n))
    seeds = []
    for node_id in ranked:
        if scores[node_id] < scores[ranked[0]] * 0.45:
            break
        node = index["nodes"][node_id]
        parent = node.get("parentId")
        if parent in seeds or (node.get("kind") == "file" and any(index["nodes"][n].get("path") == node.get("path") for n in seeds)):
            continue
        seeds.append(node_id)
        if len(seeds) == 4:
            break
    return seeds


def _graph_evidence(index, query, symbol_ids=None, direction="auto", max_depth=4, edge_kinds=None, char_budget=12000):
    """Breadth-first traversal. Containers expose members; external nodes are leaves."""
    nodes, all_edges = index["nodes"], index["edges"]
    seeds = list(dict.fromkeys(index["references"].get(n, n) for n in (symbol_ids or _rank(index, query))))[:8]
    unmatched = [n for n in seeds if n not in nodes]
    seeds = [n for n in seeds if n in nodes]
    basis = "explicit symbols" if symbol_ids else "lexical symbol matches"
    if not seeds and not symbol_ids:
        candidates = index.get("entryCandidates", [])
        conventional = [e for e in candidates if e.get("entryConvention")]
        seeds = [e["id"] for e in (conventional or candidates)[:4]]
        basis = "entry-point candidates; no lexical anchor"
        if not seeds:
            seeds = [index["references"].get(n["id"], n["id"]) for n in index["summary"].get("modules", [])[:4]]
            basis = "structural components; no entry point identified"
    actual_direction = ("outgoing" if "no lexical anchor" in basis or "no entry point" in basis else "both") if direction == "auto" else direction
    max_depth = min(max(1, max_depth), 8)
    allowed_kinds = set(edge_kinds) if edge_kinds else None
    selected, selected_edges, seen_edges = {}, [], set()
    paths, frontier, exclusions = {}, [], set()
    queue = deque((n, 0, None) for n in seeds)
    visited = set()
    scans = 0

    def ref(node_id):
        return nodes.get(node_id, {}).get("ref", node_id)

    def wire_node(node):
        # Short, commit-scoped IDs avoid repeating long qualified IDs in every edge.
        return {**{k: v for k, v in node.items() if k not in {"ref", "attributes"}},
                "id": ref(node["id"]), "parentId": ref(node.get("parentId"))}

    def wire_edge(edge):
        result = {**edge, "sourceId": ref(edge["sourceId"]), "targetId": ref(edge["targetId"])}
        if result.get("path") == nodes[edge["sourceId"]].get("path"):
            result.pop("path", None)
        return result

    def size_with(new_nodes, edge=None):
        return len(json.dumps({"nodes": [wire_node(n) for n in list(selected.values()) + new_nodes],
                               "edges": [wire_edge(e) for e in selected_edges + ([edge] if edge else [])]}, ensure_ascii=False))

    while queue:
        node_id, depth, previous = queue.popleft()
        if node_id in visited:
            continue
        node = nodes[node_id]
        if node_id not in selected:
            if len(selected) >= MAX_GRAPH_NODES or size_with([node]) > char_budget:
                frontier.append({"id": node_id, "depth": depth, "reason": "evidence budget"})
                continue
            selected[node_id] = node
        visited.add(node_id)
        if node_id not in paths or depth < paths[node_id]["depth"]:
            paths[node_id] = {"depth": depth, "via": previous}
        if node.get("kind") == "external_symbol":
            continue  # Shared library/logging sinks must not connect unrelated internal flows.

        # A class/file question includes its methods, even when only the methods have CALLS.
        if node.get("kind") in {"class", "interface", "file", "module", "package"}:
            children = index["children"].get(node_id, [])
            if len(children) > 100:
                exclusions.add("container member budget")
            for child in reversed(children[:100]):
                if child in nodes and child not in visited:
                    queue.appendleft((child, depth, node_id))

        for edge_index in index["adjacency"].get(node_id, []):
            scans += 1
            if scans > 2000:
                exclusions.add("edge scan budget")
                break
            edge = all_edges[edge_index]
            kind = edge.get("kind")
            if kind == "CONTAINS" or (allowed_kinds and kind not in allowed_kinds):
                continue
            src, tgt = edge.get("sourceId"), edge.get("targetId")
            if actual_direction == "outgoing" and src != node_id:
                continue
            if actual_direction == "incoming" and tgt != node_id:
                continue
            neighbor = tgt if src == node_id else src
            if neighbor not in nodes:
                exclusions.add("missing edge endpoint")
                continue
            if depth >= max_depth and neighbor not in selected:
                frontier.append({"id": neighbor, "depth": depth + 1, "reason": "depth limit"})
                continue
            if edge_index in seen_edges:
                continue
            additional = [] if neighbor in selected else [nodes[neighbor]]
            if len(selected) + len(additional) > MAX_GRAPH_NODES or len(selected_edges) >= MAX_GRAPH_EDGES or size_with(additional, edge) > char_budget:
                frontier.append({"id": neighbor, "depth": depth + 1, "reason": "evidence budget"})
                continue
            selected[neighbor] = nodes[neighbor]
            selected_edges.append(edge)
            seen_edges.add(edge_index)
            paths.setdefault(neighbor, {"depth": depth + 1, "via": node_id})
            if neighbor not in visited:
                queue.append((neighbor, depth + 1, node_id))
        if scans > 2000:
            break
    unfinished = [n for n in frontier if n["id"] not in visited]
    if queue:
        exclusions.add("unexpanded queued nodes")
    return {"seedIds": [ref(n) for n in seeds], "selectionBasis": basis, "unmatchedIds": unmatched,
            "nodes": [wire_node(n) for n in selected.values()], "edges": [wire_edge(e) for e in selected_edges],
            "traversal": {"algorithm": "breadth-first", "direction": actual_direction, "maxDepth": max_depth,
                          "edgeKinds": sorted(allowed_kinds) if allowed_kinds else "all non-containment relationships",
                          "paths": {ref(n): {**p, "via": ref(p["via"])} for n, p in paths.items()}, "edgesExamined": scans},
            "coverage": {"returnedNodes": len(selected), "returnedEdges": len(selected_edges),
                         "truncated": bool(unfinished or exclusions), "reasons": sorted(exclusions),
                         "frontierCount": len(unfinished), "frontier": [{**n, "id": ref(n["id"])} for n in unfinished[:8]]},
            "edgeLocationRule": "An edge without a path uses its source node's path; startLine/endLine locate the call site.",
            "limitations": "Static relationships, not execution order or proof of runtime behavior. Ownership is given by parentId/via, not CALLS."}


def _bounded_summary(index, query):
    summary = json.loads(json.dumps(index["summary"]))
    # Representative facts are context, never an invented answer about program purpose.
    for key, cap in (("modules", 6), ("imports", 12), ("entryCandidates", 4)):
        summary[key] = summary.get(key, [])[:cap]
    tokens = set(_tokenize(query)) - _STOPWORDS
    build_paths = index.get("buildFiles", [])
    build_paths = sorted(build_paths, key=lambda p: (-len(tokens & set(_tokenize(p))), p.count('/'), p))
    summary["buildFiles"] = build_paths[:4]
    summary["coverage"]["namespacesShown"] = len(summary.get("imports", []))
    while len(json.dumps(summary, ensure_ascii=False)) > 4000:
        items = [summary[key] for key in ("imports", "modules", "entryCandidates") if summary.get(key)]
        if not items:
            break
        max(items, key=lambda values: len(json.dumps(values))).pop()
        summary["responseTruncated"] = True
    summary["coverage"]["namespacesShown"] = len(summary.get("imports", []))
    return summary


def _finish(turn, result):
    size = len(json.dumps(result, ensure_ascii=False))
    turn["chars"] += size
    result["retrievalBudget"] = {"responseCharacters": size, "remainingCharacters": max(0, MAX_TURN_CHARS - turn["chars"]),
                                 "remainingSourceCharacters": max(0, MAX_SOURCE_CHARS - turn["sourceChars"]),
                                 "remainingCalls": max(0, MAX_CALLS - turn["calls"])}
    return result


async def retrieve_context(
    query: str,
    evidence_gap: str,
    tool_context: ToolContext,
    symbol_ids: list[str] | None = None,
    direction: str = "auto",
    max_depth: int = 4,
    edge_kinds: list[str] | None = None,
    source_ids: list[str] | None = None,
    source_start_line: int | None = None,
) -> dict:
    """Retrieve a connected evidence subgraph for the selected repository.

    Query describes the question or a specific unresolved fact. Optional symbol_ids
    select exact returned IDs; otherwise match symbols or start at structural entry
    candidates. BFS follows direction (auto/outgoing/incoming/both), max_depth (1-8)
    and optional edge_kinds such as CALLS or IMPORTS. Classes expose their members.
    Inspect coverage/frontier before treating a graph as complete.

    An evidence_gap or source_ids requests source grounding. source_ids can name
    returned symbol IDs or returned build paths. Reads only selected graph-backed
    windows, never directory scans. Further retrieval is allowed within the shared
    evidence budget; continue for a named gap, not to repeat or inventory files.
    source_start_line continues a long declaration with one explicit source_id.
    """
    app = tool_context.state.get("active_app")
    if not app:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}
    if direction not in {"auto", "outgoing", "incoming", "both"}:
        return {"ok": False, "error": "INVALID_DIRECTION"}
    turn = _turn(tool_context)
    if turn["busy"]:
        return {"ok": False, "error": "RETRIEVAL_IN_PROGRESS"}
    if turn["calls"] >= MAX_CALLS or turn["chars"] >= MAX_TURN_CHARS - 1000:
        return {"ok": False, "error": "RETRIEVAL_COMPLETE", "message": "Evidence budget reached. State unresolved gaps; do not invent an answer."}
    turn["calls"] += 1
    request_key = json.dumps([query, evidence_gap, symbol_ids, direction, max_depth, edge_kinds, source_ids, source_start_line])
    if request_key in turn["requests"]:
        return {"ok": False, "error": "DUPLICATE_RETRIEVAL", "message": "Reuse the previous result, or change the traversal/source target to resolve a specific gap."}
    turn["busy"] = True
    try:
        index = await _load_index(_rs.get_store(), app, tool_context.state.get("active_commit_sha"))
        available = min(MAX_RESPONSE_CHARS, MAX_TURN_CHARS - turn["chars"] - 500)
        source_requested = bool(evidence_gap.strip() or source_ids)
        summary = _bounded_summary(index, query) if not turn["overviewSent"] else None
        summary_size = len(json.dumps(summary, ensure_ascii=False)) if summary else 0
        graph_budget = max(1200, available - summary_size - (5500 if source_requested else 1500))
        graph = _graph_evidence(index, query, symbol_ids, direction, max_depth, edge_kinds, graph_budget)
        result = {"ok": True, "applicationSlug": app, "commitSha": index.get("commitSha"), "graph": graph}
        if summary:
            result["repository"] = summary
        candidates = [n for n in graph["nodes"] if n.get("path") and n.get("kind") not in {"package", "external_symbol"}]
        candidates.extend({"id": p, "path": p, "name": p, "kind": "build", "startLine": 1} for p in index.get("buildFiles", []))
        if source_requested:
            targets = {n["id"]: n for n in candidates + turn["candidates"]}
            if source_ids:
                for source_id in source_ids:
                    node = index["nodes"].get(index["references"].get(source_id, source_id))
                    if node and node.get("path") and node.get("kind") not in {"package", "external_symbol"}:
                        targets[source_id] = {**node, "id": node["ref"]}
                chosen = [targets[n] for n in source_ids if n in targets]
                result["unknownSourceIds"] = [n for n in source_ids if n not in targets]
            else:
                tokens = set(_tokenize(query + ' ' + evidence_gap)) - _STOPWORDS
                chosen = sorted(targets.values(), key=lambda n: (
                    -len(tokens & set(_tokenize(n.get("qualifiedName", '') + ' ' + n.get("name", '') + ' ' + n["path"]))),
                    n.get("kind") not in {"function", "method", "build"},
                    graph["traversal"]["paths"].get(n["id"], {}).get("depth", 99)))
            result["sourceExcerpts"], result["sourceGaps"] = [], []
            paths_this_call = set()
            for node in chosen:
                if len(paths_this_call) >= 2 or turn["sourceChars"] >= MAX_SOURCE_CHARS:
                    break
                if node["path"] not in {w[0] for w in turn["sourceWindows"]} and len({w[0] for w in turn["sourceWindows"]}) >= 4:
                    result["sourceGaps"].append({"id": node["id"], "reason": "source file budget"})
                    continue
                start = max(1, node.get("startLine") or 1)
                if source_start_line is not None and source_ids and len(source_ids) == 1:
                    if source_start_line < start or source_start_line > (node.get("endLine") or source_start_line):
                        result["sourceGaps"].append({"id": node["id"], "reason": "requested line is outside this declaration"})
                        continue
                    start = source_start_line
                end = min(node.get("endLine") or start + 79, start + 79)
                window = [node["path"], start, end]
                if window in turn["sourceWindows"] or node["path"] in paths_this_call:
                    continue
                paths_this_call.add(node["path"])
                turn["sourceWindows"].append(window)
                try:
                    excerpt = await _read_source_excerpt(node["path"], start, end, tool_context)
                except (OSError, ValueError, GoogleAPIError):
                    result["sourceGaps"].append({"id": node["id"], "reason": "source unavailable"})
                    continue
                if not excerpt.get("ok"):
                    result["sourceGaps"].append(excerpt)
                    continue
                remaining = min(MAX_SOURCE_CHARS - turn["sourceChars"], 4000,
                                available - len(json.dumps(result, ensure_ascii=False)) - 600)
                if remaining <= 0:
                    result["sourceGaps"].append({"id": node["id"], "reason": "response budget; source window not returned"})
                    turn["sourceWindows"].remove(window)
                    break
                shortened = len(excerpt["content"]) > remaining
                excerpt["content"] = excerpt["content"][:remaining]
                excerpt["endLine"] = start + max(1, len(excerpt["content"].splitlines())) - 1
                excerpt["truncated"] = excerpt["truncated"] or excerpt["endLine"] < end or shortened
                excerpt["symbolId"] = node["id"]
                turn["sourceChars"] += len(excerpt["content"])
                result["sourceExcerpts"].append(excerpt)
            if not result["sourceExcerpts"] and not result["sourceGaps"]:
                result["sourceGaps"].append({"reason": "No new source window returned; inspect prior evidence, select a symbol, or continue a truncated declaration."})
        # Keep only bounded anchors for refinement; never store the index in session state.
        turn["candidates"] = list({n["id"]: n for n in turn["candidates"] + candidates}.values())[-80:]
        turn["overviewSent"] = turn["overviewSent"] or bool(summary)
        turn["requests"].append(request_key)
        result["evidenceStatus"] = {
            "hasRelationships": bool(graph["edges"]), "hasSourceGrounding": bool(result.get("sourceExcerpts")),
            "unresolved": graph["coverage"]["truncated"] or bool(result.get("sourceGaps")) or not graph["nodes"],
            "note": "Determine sufficiency for the question. Names/edges support structure; source is needed to confirm conditions, effects and execution order."}
        return _finish(turn, result)
    except (OSError, ValueError, KeyError, TypeError, IndexError, GoogleAPIError) as exc:
        return {"ok": False, "error": "RETRIEVAL_UNAVAILABLE", "reason": type(exc).__name__,
                "message": "Stored evidence is missing, unreadable, or stale; do not infer behavior from an unrelated symbol."}
    finally:
        turn["busy"] = False
