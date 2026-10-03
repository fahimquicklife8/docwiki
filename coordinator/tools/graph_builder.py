"""Graph builder and validator. Phase 3."""

from __future__ import annotations

import re
import sys
from collections import Counter, deque
from pathlib import PurePosixPath
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


BUILD_FILES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "setup.py", "package.json",
}

INDEX_VERSION = "3.0"


def _execution_roots(nodes, edges):
    """Find execution candidates from declarations and call topology, not filenames."""
    outgoing, incoming = {}, Counter()
    for edge in edges:
        if edge.get("kind") == "CALLS" and edge.get("targetId") in nodes:
            outgoing.setdefault(edge["sourceId"], []).append(edge["targetId"])
            incoming[edge["targetId"]] += 1
    candidates = []
    for node_id, node in nodes.items():
        if node.get("kind") not in {"function", "method", "module"}:
            continue
        parent = nodes.get(node.get("parentId"), {})
        if parent.get("kind") == "class" and node.get("name") == parent.get("name"):
            continue  # A constructor with no modeled incoming calls is not an entry point.
        main = node.get("name") == "main" and (
            node.get("language") == "python" or "String[]" in node.get("signature", ""))
        module_entry = node.get("kind") == "module" and PurePosixPath(node.get("path", "")).name == "__main__.py"
        if not (main or module_entry or (outgoing.get(node_id) and not incoming[node_id])):
            continue
        seen, queue = {node_id}, deque([node_id])
        while queue and len(seen) < 256:
            for target in outgoing.get(queue.popleft(), []):
                if target not in seen and nodes[target].get("kind") != "external_symbol":
                    seen.add(target)
                    queue.append(target)
                    if len(seen) >= 256:
                        break
        candidates.append({"id": node_id, "reason": "entry-point convention" if main or module_entry else "no modeled incoming calls",
                           "entryConvention": main or module_entry, "reachableInternalSymbols": len(seen),
                           "reachabilityCapped": bool(queue)})
    candidates.sort(key=lambda n: (-int(n["entryConvention"]), -n["reachableInternalSymbols"], n["id"]))
    if not candidates:
        # Libraries/cycles may have no root; expose explicitly labeled structural seeds.
        candidates = [{"id": node_id, "reason": "outgoing-call connectivity; entry point unknown", "entryConvention": False}
                      for node_id in sorted(outgoing, key=lambda n: (-len(outgoing[n]), n))
                      if node_id in nodes and nodes[node_id].get("kind") != "external_symbol"][:8]
    return candidates


def build_symbol_index(app_slug: str, commit_sha: str, graph: dict, manifest: dict | None = None) -> dict:
    """Build one reusable index for aggregate facts, symbol ranking and adjacency."""
    manifest = manifest or {}
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    by_id = {n["id"]: n for n in nodes}
    dependencies = {}
    standard = set()
    import_count = 0
    for edge in edges:
        if edge.get("kind") != "IMPORTS":
            continue
        import_count += 1
        target = by_id.get(edge.get("targetId"), {})
        if target.get("kind") != "external_symbol":
            continue
        origin = by_id.get(edge.get("sourceId"), {})
        language = target.get("language") or origin.get("language", "")
        name = target.get("qualifiedName") or target.get("name", "")
        parts = name.split(".")
        if not parts[0]:
            continue
        if (language == "python" and parts[0] in sys.stdlib_module_names) or (
            language == "java" and parts[0] in {"java", "jdk"}):
            standard.add(parts[0] if language == "python" else ".".join(parts[:2]))
            continue
        # Group imported classes under their library namespace. Java namespace
        # groups are evidence, not a claim that a Maven artifact was identified.
        namespace = ".".join(parts[:2]) if language == "java" else parts[0]
        item = dependencies.setdefault((language, namespace), {
            "name": namespace, "language": language, "paths": set(), "evidence": [], "references": 0,
        })
        item["references"] += 1
        path = edge.get("path") or origin.get("path", "")
        if path and path not in item["paths"] and len(item["evidence"]) < 2:
            item["evidence"].append({
                "path": path, "startLine": edge.get("startLine"), "endLine": edge.get("endLine"),
                "resolution": edge.get("resolution"), "importedSymbol": name,
            })
        if path:
            item["paths"].add(path)
    ranked = sorted(dependencies.values(), key=lambda d: (-len(d["paths"]), -d["references"], d["name"]))
    imports = [{"name": d["name"], "language": d["language"], "external": True,
                "fileCount": len(d["paths"]), "referenceCount": d["references"],
                "evidence": d["evidence"]} for d in ranked[:24]]
    files = manifest.get("files", [])
    build_paths = sorted({f["path"] for f in files + manifest.get("contextFiles", [])
                          if PurePosixPath(f["path"]).name.lower() in BUILD_FILES},
                         key=lambda p: (p.count("/"), p))
    project_roots = sorted({str(PurePosixPath(p).parent) for p in build_paths})
    roots = [n for n in nodes if n.get("kind") in {"class", "module"}]
    degrees = Counter(e.get("sourceId") for e in edges if e.get("kind") != "CONTAINS")
    roots.sort(key=lambda n: (-degrees[n["id"]], n.get("path", ""), n["id"]))
    summary = {
        "ok": True, "applicationSlug": app_slug,
        "commitSha": commit_sha,
        "languages": sorted({n["language"] for n in nodes if n.get("language")}),
        "nodeKinds": dict(Counter(n.get("kind") for n in nodes)),
        "edgeKinds": dict(Counter(e.get("kind") for e in edges)),
        "modules": [{k: n.get(k) for k in ("id", "name", "path", "startLine", "endLine")} for n in roots[:8]],
        "imports": imports,
        "standardLibraryNamespaces": sorted(standard)[:12],
        "buildFiles": build_paths[:3],
        "projectLayout": {"buildRootCount": len(project_roots), "sampleBuildRoots": project_roots[:5],
                          "note": "Multiple build roots may indicate independent projects; do not imply one shared runtime stack."},
        "coverage": {"sourceFiles": len(files), "graphNodes": len(nodes), "graphEdges": len(edges),
                     "importEdgesAggregated": import_count, "externalNamespaces": len(ranked),
                     "namespacesShown": len(imports), "buildFiles": len(build_paths)},
        "truncated": {"modules": len(roots) > 8, "imports": len(ranked) > 24, "buildFiles": len(build_paths) > 3},
        "limitations": "Static Python/Java import evidence, grouped by namespace, not installed packages or versions. "
                        "Coverage is the stored snapshot, not a guarantee that every repository file was analyzed. "
                        "Unresolved external imports may also be local modules. Build files are paths, not dependency evidence.",
    }
    compact_nodes = {
        n["id"]: {key: n[key] for key in ("id", "name", "qualifiedName", "kind", "language", "parentId", "path", "startLine", "endLine", "signature", "attributes") if key in n}
        for n in nodes
    }
    references = {f"s{i}": node_id for i, node_id in enumerate(sorted(compact_nodes), 1)}
    node_refs = {node_id: ref for ref, node_id in references.items()}
    for node_id, node in compact_nodes.items():
        node["ref"] = node_refs[node_id]
    terms = {}
    adjacency = {}
    children = {}
    compact_edges = []
    for node_id, node in compact_nodes.items():
        node["signature"] = node.get("signature", "")[:500]
        for term in set(_tokenize(node.get("name", "") + " " + node.get("qualifiedName", "") + " " + node.get("path", ""))):
            terms.setdefault(term, []).append(node_id)
    for edge in edges:
        index = len(compact_edges)
        compact_edges.append({key: edge[key] for key in ("sourceId", "targetId", "kind", "resolution", "confidence", "path", "startLine", "endLine") if key in edge})
        if edge.get("kind") == "CONTAINS":
            children.setdefault(edge["sourceId"], []).append(edge["targetId"])
        for node_id in {edge.get("sourceId"), edge.get("targetId")}:
            adjacency.setdefault(node_id, []).append(index)
    # Ownership remains available even for older graphs with incomplete CONTAINS edges.
    for node_id, node in compact_nodes.items():
        parent = node.get("parentId")
        if parent in compact_nodes:
            children.setdefault(parent, [])
            if node_id not in children[parent]:
                children[parent].append(node_id)
    entries = _execution_roots(compact_nodes, compact_edges)
    summary["entryCandidates"] = [{**entry, "id": node_refs[entry["id"]], "name": compact_nodes[entry["id"]].get("qualifiedName"),
                                    "path": compact_nodes[entry["id"]].get("path"),
                                    "startLine": compact_nodes[entry["id"]].get("startLine"),
                                    "endLine": compact_nodes[entry["id"]].get("endLine")}
                                   for entry in entries[:8]]
    summary["coverage"]["entryCandidates"] = len(entries)
    for module in summary["modules"]:
        module["id"] = node_refs[module["id"]]
    summary["limitations"] += " Entry candidates are structural/conventional evidence, not proof of runtime invocation."
    return {"schemaVersion": INDEX_VERSION, "applicationSlug": app_slug,
            "commitSha": commit_sha, "summary": summary,
            "nodes": compact_nodes, "terms": terms, "edges": compact_edges, "adjacency": adjacency,
            "children": children, "entryCandidates": entries, "buildFiles": build_paths,
            "references": references}


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
