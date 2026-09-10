"""Documentation tools — used by doc_generator sub-agent (Phase 5) and coordinator (Phase 6)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.tools import repo_store as _rs
from coordinator.tools.source_tools import _get_active_app


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug_from_module_id(module_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", module_id.lower()).strip("-")[:60]


# ===========================================================================
# Documentation sub-agent tools (Phase 5)
# ===========================================================================


async def list_documentation_modules(tool_context: ToolContext) -> dict:
    """List modules for documentation.

    Files are grouped into chunks by cumulative byte size (MODULE_CHUNK_BYTES).
    This ensures each module covers a meaningful slice of the codebase regardless
    of how many packages or folders the project uses.
    """
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    chunks = _build_file_chunks(manifest.get("files", []), config.MAX_DOC_MODULES)

    modules = []
    for i, chunk_files in enumerate(chunks, 1):
        chunk_bytes = sum(f.get("bytes", 0) for f in chunk_files)
        langs = sorted({f["language"] for f in chunk_files})
        display = _chunk_display_name(i, [f["path"] for f in chunk_files])
        modules.append(
            {
                "moduleId": f"chunk-{i}",
                "displayName": display,
                "fileCount": len(chunk_files),
                "bytes": chunk_bytes,
                "languages": langs,
            }
        )

    return {"ok": True, "modules": modules}


def _build_file_chunks(files: list[dict], max_chunks: int) -> list[list[dict]]:
    """Group files into at most max_chunks chunks by cumulative byte size."""
    if not files:
        return []
    sorted_files = sorted(files, key=lambda f: f["path"])
    threshold = config.MODULE_CHUNK_BYTES
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_bytes = 0
    for f in sorted_files:
        fb = f.get("bytes", 0)
        # Start a new chunk when threshold exceeded — but never if we've hit the cap
        if current and current_bytes + fb > threshold and len(chunks) < max_chunks - 1:
            chunks.append(current)
            current = [f]
            current_bytes = fb
        else:
            current.append(f)
            current_bytes += fb
    if current:
        chunks.append(current)
    # Merge any excess chunks beyond the cap into the last one
    while len(chunks) > max_chunks:
        chunks[-2].extend(chunks.pop())
    return chunks


def _chunk_display_name(index: int, paths: list[str]) -> str:
    """Human-readable name: common directory prefix when available."""
    if not paths:
        return f"Module {index}"
    parts_list = [p.split("/") for p in paths]
    common: list[str] = []
    for group in zip(*parts_list):
        if len(set(group)) == 1:
            common.append(group[0])
        else:
            break
    prefix = "/".join(common) if common else ""
    return f"Module {index}: {prefix}" if prefix else f"Module {index}"


async def load_documentation_context(module_id: str, tool_context: ToolContext) -> dict:
    """Load bounded evidence (graph edges + source excerpts) for a module."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        graph = await store.read_json(app_slug, "graph.json")
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}

    # Determine which nodes/files belong to this module
    module_node_ids: set[str] = set()
    module_nodes: list[dict] = []

    if module_id.startswith("chunk-"):
        # Chunk-based: re-derive the file list deterministically from the manifest
        try:
            chunk_index = int(module_id.split("-", 1)[1])
        except (IndexError, ValueError):
            return {"ok": False, "error": "INVALID_MODULE_ID"}
        all_chunks = _build_file_chunks(manifest.get("files", []), config.MAX_DOC_MODULES)
        if chunk_index < 1 or chunk_index > len(all_chunks):
            return {"ok": False, "error": "INVALID_MODULE_ID"}
        module_paths: set[str] = {f["path"] for f in all_chunks[chunk_index - 1]}
        for node in graph.get("nodes", []):
            if node.get("path", "") in module_paths and node.get("kind") not in (
                "external_symbol",
            ):
                module_node_ids.add(node["id"])
                module_nodes.append(node)
    else:
        # Legacy: match by qualifiedName prefix
        module_paths = set()
        for node in graph.get("nodes", []):
            qname = node.get("qualifiedName", "")
            if (
                qname == module_id
                or qname.startswith(module_id + ".")
                or qname.startswith(module_id + "#")
            ):
                module_node_ids.add(node["id"])
                if node.get("kind") not in ("external_symbol",):
                    module_nodes.append(node)
        module_paths = {n.get("path", "") for n in module_nodes if n.get("path")}

    # Collect edges: internal + 1-hop — cap to avoid huge responses
    relevant_edges: list[dict] = []
    external_node_ids: set[str] = set()
    for edge in graph.get("edges", []):
        if len(relevant_edges) >= 200:  # cap
            break
        src, tgt = edge.get("sourceId", ""), edge.get("targetId", "")
        if src in module_node_ids or tgt in module_node_ids:
            relevant_edges.append(edge)
            if src not in module_node_ids:
                external_node_ids.add(src)
            if tgt not in module_node_ids:
                external_node_ids.add(tgt)

    # Cap nodes and external nodes to keep response size bounded
    module_nodes = module_nodes[:80]
    external_nodes = [
        n for n in graph.get("nodes", []) if n.get("id") in external_node_ids
    ][:40]

    # Load source excerpts — use DOC_CONTEXT_CHARS cap (much smaller than MAX_CONTEXT_CHARACTERS)
    source_excerpts: list[dict] = []
    total_chars = 0
    char_cap = config.DOC_CONTEXT_CHARS
    for entry in manifest.get("files", []):
        fp = entry["path"]
        if fp not in module_paths:
            continue
        try:
            content = await store.read_text(app_slug, f"source/{fp}")
        except FileNotFoundError:
            continue
        remaining = char_cap - total_chars
        if remaining <= 0:
            break
        # Per-file cap: at most 4000 chars per file to spread coverage
        per_file_cap = min(remaining, 4000)
        excerpt = content[:per_file_cap]
        source_excerpts.append(
            {"path": fp, "content": excerpt, "truncated": len(content) > per_file_cap}
        )
        total_chars += len(excerpt)

    return {
        "ok": True,
        "moduleId": module_id,
        "nodes": module_nodes,
        "edges": relevant_edges,
        "externalNodes": external_nodes,
        "sourceExcerpts": source_excerpts,
        "contextCharsUsed": total_chars,
    }


async def save_module_document(
    module_id: str,
    markdown: str,
    tool_context: ToolContext = None,
) -> dict:
    """Persist a module document."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    slug = _slug_from_module_id(module_id)
    doc_path = f"docs/modules/{slug}.md"
    await store.write_text(app_slug, doc_path, markdown)

    # Update documents.json
    await _upsert_document(
        store,
        app_slug,
        manifest.get("commitSha") or "",
        {
            "slug": slug,
            "title": module_id.replace(".", " / ").replace("#", " "),
            "kind": "module",
            "path": doc_path,
            "moduleId": module_id,
            "sourceReferences": [],
        },
    )
    return {"ok": True, "slug": slug, "path": doc_path}


async def load_overview_context(tool_context: ToolContext) -> dict:
    """Load bounded evidence for the repository overview document."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        graph = await store.read_json(app_slug, "graph.json")
        manifest = await store.read_json(app_slug, "source_manifest.json")
        meta = await store.read_json(app_slug, "metadata.json")
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}

    # Top-level nodes (packages, modules, top-level classes)
    top_nodes = [
        n for n in graph.get("nodes", []) if n.get("kind") in ("package", "module", "repository")
    ][:50]

    # README and config file contents — cap to DOC_CONTEXT_CHARS
    readme_excerpts: list[dict] = []
    total_chars = 0
    readme_patterns = {"readme", "readme.md", "readme.txt", "readme.rst"}
    for entry in manifest.get("files", []):
        if total_chars >= config.DOC_CONTEXT_CHARS:
            break
        fp = entry["path"]
        basename = fp.lower().split("/")[-1]
        is_config = basename in ("setup.py", "pyproject.toml", "pom.xml", "build.gradle")
        is_readme = basename in readme_patterns
        if not is_readme and not is_config:
            continue
        try:
            content = await store.read_text(app_slug, f"source/{fp}")
        except FileNotFoundError:
            continue
        remaining = config.DOC_CONTEXT_CHARS - total_chars
        excerpt = content[:remaining]
        readme_excerpts.append({"path": fp, "content": excerpt})
        total_chars += len(excerpt)

    # Statistics summary
    stats = graph.get("statistics", {})

    return {
        "ok": True,
        "metadata": {
            "displayName": meta.get("displayName"),
            "repositoryUrl": meta.get("repositoryUrl"),
            "languages": meta.get("languages", []),
            "statistics": meta.get("statistics", {}),
        },
        "topNodes": top_nodes,
        "graphStatistics": stats,
        "readmeExcerpts": readme_excerpts,
    }


async def save_overview_document(
    markdown: str,
    tool_context: ToolContext = None,
) -> dict:
    """Persist the overview document."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    await store.write_text(app_slug, "docs/overview.md", markdown)
    await _upsert_document(
        store,
        app_slug,
        manifest.get("commitSha") or "",
        {
            "slug": "overview",
            "title": "Overview",
            "kind": "overview",
            "path": "docs/overview.md",
            "moduleId": None,
            "sourceReferences": [],
        },
    )
    return {"ok": True, "path": "docs/overview.md"}


async def finalize_documentation(tool_context: ToolContext) -> dict:
    """Set metadata to ready, mark job succeeded, update list_apps.json."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    store = _rs.get_store()
    try:
        meta = await store.read_json(app_slug, "metadata.json")
        docs = await store.read_json(app_slug, "documents.json")
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}

    # Update metadata to ready
    meta["status"] = "ready"
    meta["statistics"]["documents"] = len(docs.get("documents", []))
    meta["updatedAt"] = _now()
    await store.write_json(app_slug, "metadata.json", meta)

    # Finalize job
    job: dict[str, Any] = {}
    try:
        job = await store.read_json(app_slug, "job.json")
    except FileNotFoundError:
        pass
    from coordinator.schemas import STAGE_PROGRESS

    job.update(
        {
            "status": "succeeded",
            "stage": "complete",
            "progressPercent": STAGE_PROGRESS["complete"],
            "message": "Documentation generation complete.",
            "completedAt": _now(),
            "updatedAt": _now(),
        }
    )
    await store.write_json(app_slug, "job.json", job)

    # Update list_apps.json
    await _update_catalog(store, app_slug, meta)

    return {"ok": True, "applicationSlug": app_slug, "status": "ready"}


# ===========================================================================
# Coordinator document retrieval tools (Phase 6)
# ===========================================================================


async def get_repository_overview(tool_context: ToolContext) -> dict:
    """Return the overview document for the active application."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}
    store = _rs.get_store()
    return await _get_doc_by_kind(store, app_slug, "overview")


async def list_documents(tool_context: ToolContext) -> dict:
    """List available documents for the active application."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}
    store = _rs.get_store()
    try:
        docs = await store.read_json(app_slug, "documents.json")
    except FileNotFoundError:
        return {"ok": True, "documents": []}
    return {"ok": True, "documents": docs.get("documents", [])}


async def get_document(slug: str, tool_context: ToolContext) -> dict:
    """Retrieve a specific document by slug."""
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}
    store = _rs.get_store()
    try:
        docs = await store.read_json(app_slug, "documents.json")
    except FileNotFoundError:
        return {"ok": False, "error": "DOCUMENTS_NOT_FOUND"}

    for doc in docs.get("documents", []):
        if doc.get("slug") == slug:
            try:
                content = await store.read_text(app_slug, doc["path"])
            except FileNotFoundError:
                return {"ok": False, "error": "DOCUMENT_FILE_NOT_FOUND", "slug": slug}
            return {"ok": True, "document": doc, "content": content}
    return {"ok": False, "error": "DOCUMENT_NOT_FOUND", "slug": slug}


# ===========================================================================
# Helpers
# ===========================================================================


async def _upsert_document(store, app_slug: str, commit_sha: str, doc: dict) -> None:
    try:
        docs = await store.read_json(app_slug, "documents.json")
    except FileNotFoundError:
        docs = {
            "schemaVersion": "1.0",
            "applicationSlug": app_slug,
            "commitSha": commit_sha,
            "documents": [],
        }

    existing = docs.get("documents", [])
    updated = [d for d in existing if d.get("slug") != doc["slug"]]
    updated.append(doc)
    docs["documents"] = updated
    await store.write_json(app_slug, "documents.json", docs)


async def _get_doc_by_kind(store, app_slug: str, kind: str) -> dict:
    try:
        docs = await store.read_json(app_slug, "documents.json")
    except FileNotFoundError:
        return {"ok": False, "error": "DOCUMENTS_NOT_FOUND"}
    for doc in docs.get("documents", []):
        if doc.get("kind") == kind:
            try:
                content = await store.read_text(app_slug, doc["path"])
            except FileNotFoundError:
                return {"ok": False, "error": "DOCUMENT_FILE_NOT_FOUND"}
            return {"ok": True, "document": doc, "content": content}
    return {"ok": False, "error": f"{kind.upper()}_NOT_FOUND"}


async def _update_catalog(store, app_slug: str, meta: dict) -> None:
    try:
        catalog = await store.read_json(None, "list_apps.json")
        if not isinstance(catalog.get("applications"), list):
            raise ValueError
    except (FileNotFoundError, ValueError):
        catalog = {"schemaVersion": "1.0", "updatedAt": _now(), "applications": []}

    apps = [a for a in catalog["applications"] if a.get("applicationSlug") != app_slug]
    apps.append(
        {
            "applicationSlug": app_slug,
            "displayName": meta.get("displayName", app_slug),
            "repositoryUrl": meta.get("repositoryUrl", ""),
            "branch": meta.get("resolvedBranch") or meta.get("requestedBranch", ""),
            "commitSha": meta.get("commitSha", ""),
            "status": meta.get("status", "ready"),
            "languages": meta.get("languages", []),
            "updatedAt": _now(),
        }
    )
    catalog["applications"] = apps
    catalog["updatedAt"] = _now()
    try:
        await store.write_json(None, "list_apps.json", catalog)
    except Exception:
        pass
