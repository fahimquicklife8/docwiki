"""Documentation tools — used by doc_generator sub-agent (Phase 5) and coordinator (Phase 6)."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from datetime import UTC, datetime
from typing import Any

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.schemas import STAGE_PROGRESS
from coordinator.tools import repo_store as _rs
from coordinator.tools.source_tools import _get_active_app
from coordinator.tools.source_inventory import PROJECT_FILES


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug_from_module_id(module_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", module_id.lower()).strip("-")[:60]


# ===========================================================================
# Documentation sub-agent tools (Phase 5)
# ===========================================================================


async def list_documentation_modules(tool_context: ToolContext) -> dict:
    """List modules for documentation.

    Files are grouped by package/directory, then bounded by size and page count.
    Display names are suggestions; the writer chooses a specific responsibility
    title from module evidence and uses it as the document's H1.
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
                "moduleId": f"Module-{i}",
                "displayName": display,
                "fileCount": len(chunk_files),
                "bytes": chunk_bytes,
                "languages": langs,
                "samplePaths": [f["path"] for f in chunk_files[:8]],
            }
        )

    return {"ok": True, "modules": modules}


def _build_file_chunks(files: list[dict], max_chunks: int) -> list[list[dict]]:
    """Keep package boundaries where possible, with deterministic bounded pages."""
    if not files:
        return []
    max_chunks = max(1, max_chunks)
    sorted_files = sorted(files, key=lambda f: f["path"])
    threshold = config.MODULE_CHUNK_BYTES
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_bytes = 0
    for f in sorted_files:
        fb = f.get("bytes", 0)
        package_changed = current and PurePosixPath(f["path"]).parent != PurePosixPath(current[-1]["path"]).parent
        if current and (package_changed or current_bytes + fb > threshold):
            chunks.append(current)
            current = [f]
            current_bytes = fb
        else:
            current.append(f)
            current_bytes += fb
    if current:
        chunks.append(current)
    # Merge the smallest adjacent groups instead of making one huge final page.
    while len(chunks) > max_chunks:
        i = min(range(len(chunks) - 1), key=lambda j: sum(
            f.get("bytes", 0) for chunk in chunks[j:j + 2] for f in chunk))
        chunks[i].extend(chunks.pop(i + 1))
    return chunks


def _chunk_display_name(index: int, paths: list[str]) -> str:
    """Suggest a page name from meaningful package names or source file stems."""
    if not paths:
        return "Implementation details"
    generic = {"src", "main", "java", "python", "com", "org", "net", "lib"}
    names = []
    for path in paths:
        p = PurePosixPath(path)
        parts = [part for part in p.parts[:-1] if part not in generic]
        name = parts[-1] if parts else p.stem.strip("_")
        name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").replace("-", " ").strip()
        name = {"tests": "Testing and verification", "test": "Testing and verification",
                "controllers": "Request handling", "services": "Application services",
                "repositories": "Data access"}.get(name.lower(), name[:1].upper() + name[1:])
        if name and name not in names:
            names.append(name)
    return " and ".join(names[:2]) or "Implementation details"


def _document_title(markdown: str, fallback: str, title: str = "") -> str:
    """Prefer an agent-authored title/H1, while rejecting old numbered labels."""
    heading = re.search(r"^#\s+(.+?)\s*#*\s*$", markdown, re.M)
    for candidate in (title, heading.group(1) if heading else "", fallback):
        candidate = re.sub(r"^(?:module|chunk)[\s-]+\d+\s*[:\-]?\s*", "", candidate.strip(), flags=re.I)
        candidate = re.sub(r"[`*_]", "", candidate).strip()
        if candidate and candidate.lower() not in {"src", "module", "overview", "overview.md"}:
            return candidate[:120]
    return "Implementation details"


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

    if module_id.lower().startswith(("chunk-", "module-")):
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

    if not source_excerpts:
        return {"ok": False, "error": "MODULE_EVIDENCE_NOT_FOUND", "moduleId": module_id}

    return {
        "ok": True,
        "moduleId": module_id,
        "suggestedTitle": _chunk_display_name(0, sorted(module_paths)),
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
    title: str = "",
) -> dict:
    """Persist a focused deep-dive page. Supply a descriptive title or start the
    Markdown with a responsibility-based H1; that title appears in navigation.
    Keep the discovered module_id unchanged so references remain stable.
    """
    app_slug = _get_active_app(tool_context)
    if not app_slug:
        return {"ok": False, "error": "NO_ACTIVE_APPLICATION"}

    if not markdown.strip():
        return {"ok": False, "error": "EMPTY_DOCUMENT"}

    store = _rs.get_store()
    try:
        manifest = await store.read_json(app_slug, "source_manifest.json")
    except FileNotFoundError:
        return {"ok": False, "error": "MANIFEST_NOT_FOUND"}

    slug = _slug_from_module_id(module_id)
    fallback = module_id.replace(".", " / ").replace("#", " ")
    if re.fullmatch(r"(?:chunk|module)-\d+", module_id, re.I):
        chunks = _build_file_chunks(manifest.get("files", []), config.MAX_DOC_MODULES)
        index = int(module_id.split("-")[1]) - 1
        fallback = _chunk_display_name(index, [f["path"] for f in chunks[index]]) if 0 <= index < len(chunks) else "Implementation details"
    display_title = _document_title(markdown, fallback, title)
    doc_path = f"docs/modules/{slug}.md"
    await store.write_text(app_slug, doc_path, markdown)

    # Update documents.json
    await _upsert_document(
        store,
        app_slug,
        manifest.get("commitSha") or "",
        {
            "slug": slug,
            "title": display_title,
            "kind": "module",
            "path": doc_path,
            "moduleId": module_id,
            "sourceReferences": [],
        },
    )
    return {"ok": True, "slug": slug, "path": doc_path, "title": display_title}


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
    for entry in manifest.get("contextFiles", []) + manifest.get("files", []):
        if total_chars >= config.DOC_CONTEXT_CHARS:
            break
        fp = entry["path"]
        basename = fp.lower().split("/")[-1]
        is_config = basename in PROJECT_FILES or basename == "setup.py"
        is_readme = basename in readme_patterns
        if not is_readme and not is_config:
            continue
        try:
            content = await store.read_text(app_slug, f"source/{fp}")
        except FileNotFoundError:
            continue
        remaining = config.DOC_CONTEXT_CHARS - total_chars
        excerpt = content[:min(remaining, 4000)]
        readme_excerpts.append({"path": fp, "content": excerpt,
                                "startLine": 1, "endLine": len(excerpt.splitlines()),
                                "truncated": len(excerpt) < len(content)})
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

    if not markdown.strip():
        return {"ok": False, "error": "EMPTY_DOCUMENT"}

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

    # An index entry alone is not proof that generation and persistence succeeded.
    documents = docs.get("documents", [])
    failures = []
    if not any(d.get("kind") == "overview" and d.get("path") == "docs/overview.md"
               for d in documents):
        failures.append("OVERVIEW_NOT_FOUND")
    modules = await list_documentation_modules(tool_context)
    if not modules.get("ok"):
        failures.append(modules["error"])
    else:
        for module in modules["modules"]:
            module_id = module["moduleId"].lower()
            if not any(d.get("kind") == "module" and
                       str(d.get("moduleId", "")).lower().replace("chunk-", "module-", 1) == module_id
                       for d in documents):
                failures.append(f"MODULE_DOCUMENT_NOT_FOUND:{module_id}")
    for document in documents:
        path = document.get("path")
        try:
            if not path or not (await store.read_text(app_slug, path)).strip():
                failures.append(f"EMPTY_DOCUMENT:{document.get('slug')}")
        except (OSError, ValueError):
            failures.append(f"DOCUMENT_UNAVAILABLE:{document.get('slug')}")
    if failures:
        meta.update(status="failed", lastError="DOCUMENTATION_INCOMPLETE", updatedAt=_now())
        await store.write_json(app_slug, "metadata.json", meta)
        try:
            job = await store.read_json(app_slug, "job.json")
        except FileNotFoundError:
            job = {}
        job.update(status="failed", stage="generating_documentation",
                   error="DOCUMENTATION_INCOMPLETE", message="Required documentation is unavailable.",
                   progressPercent=STAGE_PROGRESS["generating_documentation"],
                   completedAt=_now(), updatedAt=_now())
        await store.write_json(app_slug, "job.json", job)
        await _update_catalog(store, app_slug, meta)
        return {"ok": False, "error": "DOCUMENTATION_INCOMPLETE", "failures": failures}

    # Update metadata to ready only after all required documents can be read.
    meta["status"] = "ready"
    meta["lastError"] = None
    meta["statistics"]["documents"] = len(docs.get("documents", []))
    meta["updatedAt"] = _now()
    await store.write_json(app_slug, "metadata.json", meta)

    # Finalize job
    job: dict[str, Any] = {}
    try:
        job = await store.read_json(app_slug, "job.json")
    except FileNotFoundError:
        pass

    job.update(
        {
            "status": "succeeded",
            "stage": "complete",
            "progressPercent": STAGE_PROGRESS["complete"],
            "message": "Documentation generation complete.",
            "error": None,
            "completedAt": _now(),
            "updatedAt": _now(),
        }
    )
    await store.write_json(app_slug, "job.json", job)

    # Update list_apps.json
    await _update_catalog(store, app_slug, meta)

    return {"ok": True, "applicationSlug": app_slug, "status": "ready"}


# ===========================================================================
# Document navigation for the UI
# ===========================================================================


async def document_navigation(store, app_slug: str, documents: list[dict]) -> list[dict]:
    """Resolve legacy numbered titles for the UI without rewriting saved artifacts."""
    result = []
    for doc in documents:
        item = dict(doc)
        if item.get("kind") == "overview":
            item["title"] = "Overview"
        elif re.match(r"^(?:module|chunk)[ -]?\d+\b", item.get("title", ""), re.I):
            try:
                markdown = await store.read_text(app_slug, item["path"])
            except (KeyError, FileNotFoundError):
                markdown = ""
            cited_paths = re.findall(r"\[([^\[\]\n]+\.(?:py|java)):\d+(?:-\d+)?\]", markdown)
            fallback = _chunk_display_name(0, cited_paths) if cited_paths else item.get("title", "Implementation details")
            item["title"] = _document_title(markdown, fallback)
        result.append(item)
    return result


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
