"""Onboarding tool — orchestrates repository download, parsing, and graph build. Phases 2–4."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from coordinator import config
from coordinator.schemas import make_slug, slug_with_collision_suffix
from coordinator.tools import repo_store as _rs
from coordinator.tools.analyzer_base import RelationshipSet, SymbolTable
from coordinator.tools.github_archive import (
    download_archive,
    resolve_branch_and_commit,
    safe_extract,
    validate_github_url,
)
from coordinator.tools.graph_builder import build_graph, build_symbol_index, validate_graph
from coordinator.tools.java_analyzer import JavaAnalyzer
from coordinator.tools.python_analyzer import PythonAnalyzer
from coordinator.tools.source_inventory import inventory_source_files


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_PYTHON_ANALYZER = PythonAnalyzer()
_JAVA_ANALYZER = JavaAnalyzer()


async def onboard_repository(
    repository_url: str,
    branch: str | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """Download, analyse, and persist a public GitHub repository.

    Updates job.json at every stage.  Returns analysis_ready on success
    so the coordinator can transfer to doc_generator.

    Stable error codes are returned (never raw tracebacks).
    """
    store = _rs.get_store()
    job_id = str(uuid.uuid4())
    now = _now()
    app_slug: str | None = None
    temp_dir: Path | None = None

    async def _write_job(stage: str, status: str, msg: str, error: str | None = None) -> None:
        if app_slug is None:
            return
        from coordinator.schemas import STAGE_PROGRESS

        progress = STAGE_PROGRESS.get(stage, 0)
        job = {
            "schemaVersion": "1.0",
            "jobId": job_id,
            "applicationSlug": app_slug,
            "type": "onboarding",
            "status": status,
            "stage": stage,
            "progressPercent": progress,
            "message": msg,
            "attempt": 1,
            "error": error,
            "createdAt": now,
            "startedAt": now,
            "completedAt": _now() if status in ("succeeded", "failed") else None,
            "updatedAt": _now(),
        }
        try:
            await store.write_json(app_slug, "job.json", job)
        except Exception:
            pass  # never block on job write failure

    try:
        # ── Stage: validating ────────────────────────────────────────────
        try:
            owner, repo = validate_github_url(repository_url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "status": "failed"}

        app_slug = make_slug(owner, repo)
        # Collision detection
        existing_slugs = await store.list_application_slugs()
        if app_slug in existing_slugs:
            try:
                existing_meta = await store.read_json(app_slug, "metadata.json")
                if (
                    existing_meta.get("repositoryUrl", "").rstrip("/").lower()
                    != repository_url.rstrip("/").lower()
                ):
                    canonical = f"https://github.com/{owner}/{repo}"
                    app_slug = slug_with_collision_suffix(canonical, app_slug)
            except FileNotFoundError:
                pass

        await _write_job("validating", "running", "Validating repository URL.")
        meta_init = {
            "schemaVersion": "1.0",
            "applicationSlug": app_slug,
            "displayName": f"{owner}/{repo}",
            "repositoryOwner": owner,
            "repositoryName": repo,
            "repositoryUrl": f"https://github.com/{owner}/{repo}",
            "requestedBranch": branch,
            "resolvedBranch": branch,
            "commitSha": None,
            "status": "queued",
            "languages": [],
            "statistics": {
                "sourceFiles": 0,
                "sourceBytes": 0,
                "graphNodes": 0,
                "graphEdges": 0,
                "documents": 0,
            },
            "lastError": None,
            "createdAt": now,
            "updatedAt": now,
        }
        await store.write_json(app_slug, "metadata.json", meta_init)

        # ── Stage: downloading ───────────────────────────────────────────
        await _write_job("downloading", "running", "Resolving branch and downloading archive.")
        try:
            resolved_branch, commit_sha = await resolve_branch_and_commit(owner, repo, branch)
        except ValueError as exc:
            err = str(exc)
            await _write_job("validating", "failed", err, err)
            await _update_meta_failed(store, app_slug, err, meta_init, now)
            return {"ok": False, "error": err, "status": "failed"}

        temp_dir = config.TEMP_ROOT / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        archive_path = temp_dir / "archive.tar.gz"

        try:
            await download_archive(owner, repo, commit_sha, archive_path)
        except ValueError as exc:
            err = str(exc)
            await _write_job("downloading", "failed", err, err)
            await _update_meta_failed(store, app_slug, err, meta_init, now)
            return {"ok": False, "error": err, "status": "failed"}

        # ── Stage: inventory ─────────────────────────────────────────────
        await _write_job("inventory", "running", "Extracting and inventorying source files.")
        try:
            repo_root = safe_extract(archive_path, temp_dir / "extracted")
        except ValueError as exc:
            err = str(exc)
            await _write_job("inventory", "failed", err, err)
            await _update_meta_failed(store, app_slug, err, meta_init, now)
            return {"ok": False, "error": err, "status": "failed"}

        try:
            file_entries = inventory_source_files(
                str(repo_root),
                max_files=config.MAX_SOURCE_FILES,
                max_total_bytes=config.MAX_TOTAL_SOURCE_BYTES,
                max_single_bytes=config.MAX_SINGLE_FILE_BYTES,
            )
        except ValueError as exc:
            err = str(exc)
            await _write_job("inventory", "failed", err, err)
            await _update_meta_failed(store, app_slug, err, meta_init, now)
            return {"ok": False, "error": err, "status": "failed"}

        # Persist source files
        total_bytes = 0
        for entry in file_entries:
            src_path = repo_root / entry["path"]
            content = src_path.read_bytes()
            await store.write_text(
                app_slug, f"source/{entry['path']}", content.decode("utf-8", errors="replace")
            )
            total_bytes += entry["bytes"]

        # Write source manifest
        manifest = {
            "schemaVersion": "1.0",
            "applicationSlug": app_slug,
            "commitSha": commit_sha,
            "files": file_entries,
        }
        await store.write_json(app_slug, "source_manifest.json", manifest)

        # ── Stage: parsing ───────────────────────────────────────────────
        await _write_job("parsing", "running", "Parsing declarations.")
        languages_used: set[str] = set()
        symbol_table = SymbolTable()

        for entry in file_entries:
            src_path = repo_root / entry["path"]
            try:
                source = src_path.read_bytes()
            except OSError:
                continue
            lang = entry["language"]
            languages_used.add(lang)
            analyzer = _PYTHON_ANALYZER if lang == "python" else _JAVA_ANALYZER
            try:
                analyzer.pass1_declarations(entry["path"], source, symbol_table)
            except Exception:
                # Never let a parse error abort the whole onboarding
                pass

        # ── Stage: building_graph ────────────────────────────────────────
        await _write_job("building_graph", "running", "Resolving call relationships.")
        relationships = RelationshipSet()

        for entry in file_entries:
            src_path = repo_root / entry["path"]
            try:
                source = src_path.read_bytes()
            except OSError:
                continue
            lang = entry["language"]
            analyzer = _PYTHON_ANALYZER if lang == "python" else _JAVA_ANALYZER
            try:
                analyzer.pass2_relationships(entry["path"], source, symbol_table, relationships)
            except Exception:
                pass

        graph = build_graph(app_slug, commit_sha, symbol_table, relationships)

        try:
            validate_graph(graph, config.MAX_GRAPH_NODES, config.MAX_GRAPH_EDGES)
        except ValueError as exc:
            err = str(exc).split(":")[0]
            await _write_job("building_graph", "failed", err, err)
            await _update_meta_failed(store, app_slug, err, meta_init, now)
            return {"ok": False, "error": err, "status": "failed"}

        symbol_index = build_symbol_index(app_slug, commit_sha, graph)

        # ── Stage: persisting ────────────────────────────────────────────
        await _write_job("persisting", "running", "Persisting graph and symbol index.")
        await store.write_json(app_slug, "graph.json", graph)
        await store.write_json(app_slug, "symbol_index.json", symbol_index)

        # Initialise empty documents.json
        docs_init = {
            "schemaVersion": "1.0",
            "applicationSlug": app_slug,
            "commitSha": commit_sha,
            "documents": [],
        }
        await store.write_json(app_slug, "documents.json", docs_init)

        # Update metadata
        stats = {
            "sourceFiles": len(file_entries),
            "sourceBytes": total_bytes,
            "graphNodes": len(graph["nodes"]),
            "graphEdges": len(graph["edges"]),
            "documents": 0,
        }
        meta_updated = {
            **meta_init,
            "resolvedBranch": resolved_branch,
            "commitSha": commit_sha,
            "status": "processing",
            "languages": sorted(languages_used),
            "statistics": stats,
            "updatedAt": _now(),
        }
        await store.write_json(app_slug, "metadata.json", meta_updated)

        # Set job stage to generating_documentation — doc_generator takes over
        await _write_job(
            "generating_documentation", "running", "Awaiting documentation generation."
        )

        # Set active_app in session so doc_generator tools can access it immediately
        if tool_context is not None:
            tool_context.state["active_app"] = app_slug
            tool_context.state["active_commit_sha"] = commit_sha

        return {
            "ok": True,
            "status": "analysis_ready",
            "applicationSlug": app_slug,
            "commitSha": commit_sha,
            "languages": sorted(languages_used),
            "statistics": stats,
        }

    except Exception:
        # Log the real exception server-side so it appears in adk web / server logs.
        # Never expose the raw traceback to the model.
        import traceback

        logging.exception("onboard_repository unhandled exception (app_slug=%r): %s", app_slug, traceback.format_exc())
        err = "STORAGE_FAILED"
        if app_slug:
            try:
                await _write_job("validating", "failed", err, err)
            except Exception:
                pass
        return {"ok": False, "error": err, "status": "failed"}

    finally:
        if temp_dir and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


async def _update_meta_failed(store, app_slug: str, error: str, meta_init: dict, now: str) -> None:
    try:
        existing = await store.read_json(app_slug, "metadata.json")
        # Preserve ready status if a previous successful onboarding exists
        if existing.get("status") == "ready":
            return
    except FileNotFoundError:
        pass
    meta_fail = {**meta_init, "status": "failed", "lastError": error, "updatedAt": _now()}
    try:
        await store.write_json(app_slug, "metadata.json", meta_fail)
    except Exception:
        pass
