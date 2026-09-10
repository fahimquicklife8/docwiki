"""Storage abstraction for DocWiki repository artifacts.

Provides:
  - RepoStore      — Abstract base defining the storage interface
  - LocalRepoStore — Backed by coordinator/repos/ on the local filesystem
  - GcsRepoStore   — Backed by gs://{BUCKET}/repos/ (Phase 1)
  - get_store()    — Factory that returns the configured store instance
"""

from __future__ import annotations

import json
import posixpath
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from coordinator.schemas import ALLOWED_APP_JSON_FILENAMES, CATALOG_FILENAME

# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

_SAFE_PREFIXES = ("source/", "docs/")


def _validate_app_json_filename(filename: str) -> None:
    if filename != CATALOG_FILENAME and filename not in ALLOWED_APP_JSON_FILENAMES:
        raise ValueError(
            f"Disallowed JSON filename {filename!r}. "
            f"Must be one of {ALLOWED_APP_JSON_FILENAMES | {CATALOG_FILENAME}}"
        )


def _validate_artifact_path(relative_path: str) -> None:
    """Reject paths that escape the application prefix or use unsafe segments."""
    if not any(relative_path.startswith(p) for p in _SAFE_PREFIXES):
        raise ValueError(f"Artifact path {relative_path!r} must start with 'source/' or 'docs/'")
    normalised = posixpath.normpath(relative_path)
    if ".." in normalised.split("/"):
        raise ValueError(f"Path traversal detected in {relative_path!r}")
    if posixpath.isabs(normalised):
        raise ValueError(f"Absolute paths are not allowed: {relative_path!r}")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class RepoStore(ABC):
    """Abstract storage interface for repository artifacts."""

    @abstractmethod
    async def read_json(self, app_slug: str | None, filename: str) -> dict[str, Any]: ...

    @abstractmethod
    async def write_json(
        self, app_slug: str | None, filename: str, value: dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def read_text(self, app_slug: str, relative_path: str) -> str: ...

    @abstractmethod
    async def write_text(self, app_slug: str, relative_path: str, value: str) -> None: ...

    @abstractmethod
    async def list_application_slugs(self) -> list[str]: ...

    @abstractmethod
    async def list_paths(self, app_slug: str, prefix: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# Local filesystem implementation
# ---------------------------------------------------------------------------


class LocalRepoStore(RepoStore):
    """RepoStore backed by the local coordinator/repos/ directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _catalog_path(self) -> Path:
        return self._root / CATALOG_FILENAME

    def _app_dir(self, app_slug: str) -> Path:
        return self._root / app_slug

    def _json_path(self, app_slug: str | None, filename: str) -> Path:
        _validate_app_json_filename(filename)
        if app_slug is None:
            assert filename == CATALOG_FILENAME
            return self._catalog_path()
        return self._app_dir(app_slug) / filename

    def _artifact_path(self, app_slug: str, relative_path: str) -> Path:
        _validate_artifact_path(relative_path)
        resolved = (self._app_dir(app_slug) / relative_path).resolve()
        app_root = self._app_dir(app_slug).resolve()
        if not str(resolved).startswith(str(app_root)):
            raise ValueError(f"Path escape detected for {relative_path!r}")
        return resolved

    async def read_json(self, app_slug: str | None, filename: str) -> dict[str, Any]:
        path = self._json_path(app_slug, filename)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    async def write_json(self, app_slug: str | None, filename: str, value: dict[str, Any]) -> None:
        path = self._json_path(app_slug, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def read_text(self, app_slug: str, relative_path: str) -> str:
        path = self._artifact_path(app_slug, relative_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return path.read_text(encoding="utf-8")

    async def write_text(self, app_slug: str, relative_path: str, value: str) -> None:
        path = self._artifact_path(app_slug, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    async def list_application_slugs(self) -> list[str]:
        if not self._root.exists():
            return []
        return [
            d.name for d in self._root.iterdir() if d.is_dir() and (d / "metadata.json").exists()
        ]

    async def list_paths(self, app_slug: str, prefix: str) -> list[str]:
        _validate_artifact_path(prefix if prefix.endswith("/") else prefix + "/x")
        base = self._app_dir(app_slug) / prefix
        if not base.exists():
            return []
        result = []
        for p in base.rglob("*"):
            if p.is_file() and p.name != ".gitkeep":
                rel = p.relative_to(self._app_dir(app_slug)).as_posix()
                result.append(rel)
        return sorted(result)


# ---------------------------------------------------------------------------
# GCS implementation (Phase 1)
# ---------------------------------------------------------------------------


class GcsRepoStore(RepoStore):
    """RepoStore backed by Google Cloud Storage."""

    def __init__(self, bucket: str, prefix: str = "repos") -> None:
        self._bucket_name = bucket
        self._prefix = prefix.rstrip("/")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import storage  # type: ignore

            self._client = storage.Client()
        return self._client

    def _bucket(self):
        return self._get_client().bucket(self._bucket_name)

    def _json_blob_name(self, app_slug: str | None, filename: str) -> str:
        _validate_app_json_filename(filename)
        if app_slug is None:
            return f"{self._prefix}/{filename}"
        return f"{self._prefix}/{app_slug}/{filename}"

    def _artifact_blob_name(self, app_slug: str, relative_path: str) -> str:
        _validate_artifact_path(relative_path)
        return f"{self._prefix}/{app_slug}/{relative_path}"

    async def read_json(self, app_slug: str | None, filename: str) -> dict[str, Any]:
        import asyncio

        blob_name = self._json_blob_name(app_slug, filename)
        bucket = self._bucket()
        blob = bucket.blob(blob_name)
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, blob.download_as_text)
        except Exception as exc:
            raise FileNotFoundError(f"GCS blob not found: {blob_name}") from exc
        return json.loads(text)

    async def write_json(self, app_slug: str | None, filename: str, value: dict[str, Any]) -> None:
        import asyncio

        blob_name = self._json_blob_name(app_slug, filename)
        bucket = self._bucket()
        blob = bucket.blob(blob_name)
        data = json.dumps(value, ensure_ascii=False, indent=2)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: blob.upload_from_string(data, content_type="application/json")
        )

    async def read_text(self, app_slug: str, relative_path: str) -> str:
        import asyncio

        blob_name = self._artifact_blob_name(app_slug, relative_path)
        bucket = self._bucket()
        blob = bucket.blob(blob_name)
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, blob.download_as_text)
        except Exception as exc:
            raise FileNotFoundError(f"GCS blob not found: {blob_name}") from exc

    async def write_text(self, app_slug: str, relative_path: str, value: str) -> None:
        import asyncio

        blob_name = self._artifact_blob_name(app_slug, relative_path)
        bucket = self._bucket()
        blob = bucket.blob(blob_name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: blob.upload_from_string(value, content_type="text/plain; charset=utf-8")
        )

    async def list_application_slugs(self) -> list[str]:
        import asyncio

        prefix = self._prefix + "/"
        loop = asyncio.get_event_loop()
        blobs = await loop.run_in_executor(
            None,
            lambda: list(
                self._get_client().list_blobs(self._bucket_name, prefix=prefix, delimiter="/")
            ),
        )
        slugs = set()
        for blob in blobs:
            # Extract slug from path like repos/{slug}/metadata.json
            parts = blob.name[len(prefix) :].split("/")
            if len(parts) >= 2 and parts[1] == "metadata.json":
                slugs.add(parts[0])
        return sorted(slugs)

    async def list_paths(self, app_slug: str, prefix: str) -> list[str]:
        import asyncio

        _validate_artifact_path(prefix if prefix.endswith("/") else prefix + "/x")
        blob_prefix = f"{self._prefix}/{app_slug}/{prefix}"
        loop = asyncio.get_event_loop()
        blobs = await loop.run_in_executor(
            None,
            lambda: list(self._get_client().list_blobs(self._bucket_name, prefix=blob_prefix)),
        )
        strip = f"{self._prefix}/{app_slug}/"
        return sorted(b.name[len(strip) :] for b in blobs if not b.name.endswith("/"))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_store() -> RepoStore:
    """Return the configured RepoStore based on DOCWIKI_STORAGE_MODE."""
    import os

    from coordinator import config

    mode = os.environ.get("DOCWIKI_STORAGE_MODE", config.DOCWIKI_STORAGE_MODE)
    if mode == "gcs":
        bucket = os.environ.get("DOCWIKI_BUCKET", config.DOCWIKI_BUCKET)
        if not bucket:
            raise RuntimeError("DOCWIKI_BUCKET must be set when DOCWIKI_STORAGE_MODE=gcs")
        prefix = os.environ.get("DOCWIKI_GCS_PREFIX", config.DOCWIKI_GCS_PREFIX)
        return GcsRepoStore(bucket=bucket, prefix=prefix)
    return LocalRepoStore(root=config.LOCAL_REPOS_ROOT)
