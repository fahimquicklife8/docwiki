"""Pydantic schemas for all DocWiki JSON artifacts (schemaVersion 1.0)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Allowed fixed filenames at the application level
# ---------------------------------------------------------------------------
ALLOWED_APP_JSON_FILENAMES: frozenset[str] = frozenset(
    {
        "metadata.json",
        "job.json",
        "graph.json",
        "symbol_index.json",
        "source_manifest.json",
        "documents.json",
    }
)

CATALOG_FILENAME = "list_apps.json"

# ---------------------------------------------------------------------------
# Application slug
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def make_slug(owner: str, repository: str) -> str:
    """Derive a stable slug from {owner}/{repository}."""
    raw = f"{owner}-{repository}"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    # Truncate to 63 characters (RFC label limit)
    if len(slug) > 63:
        slug = slug[:63].rstrip("-")
    return slug


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


def slug_with_collision_suffix(canonical_url: str, base_slug: str) -> str:
    """Return base_slug + 8-char SHA-256 suffix for collision resolution."""
    h = hashlib.sha256(canonical_url.encode()).hexdigest()[:8]
    suffix = f"-{h}"
    base = base_slug[: 63 - len(suffix)].rstrip("-")
    return f"{base}{suffix}"


# ---------------------------------------------------------------------------
# Application catalog entry (inside list_apps.json)
# ---------------------------------------------------------------------------
class AppCatalogEntry(BaseModel):
    applicationSlug: str
    displayName: str
    repositoryUrl: str
    branch: str
    commitSha: str
    status: str
    languages: list[str]
    updatedAt: str


class ListAppsSchema(BaseModel):
    schemaVersion: str = "1.0"
    updatedAt: str
    applications: list[AppCatalogEntry] = []


# ---------------------------------------------------------------------------
# metadata.json
# ---------------------------------------------------------------------------
class MetadataStatistics(BaseModel):
    sourceFiles: int = 0
    sourceBytes: int = 0
    graphNodes: int = 0
    graphEdges: int = 0
    documents: int = 0


class MetadataSchema(BaseModel):
    schemaVersion: str = "1.0"
    applicationSlug: str
    displayName: str
    repositoryOwner: str
    repositoryName: str
    repositoryUrl: str
    requestedBranch: str | None = None
    resolvedBranch: str | None = None
    commitSha: str | None = None
    status: str  # queued | processing | ready | failed
    languages: list[str] = []
    statistics: MetadataStatistics = MetadataStatistics()
    lastError: str | None = None
    createdAt: str
    updatedAt: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        allowed = {"queued", "processing", "ready", "failed"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


# ---------------------------------------------------------------------------
# job.json
# ---------------------------------------------------------------------------
STAGE_PROGRESS: dict[str, int] = {
    "validating": 5,
    "downloading": 15,
    "inventory": 25,
    "parsing": 40,
    "building_graph": 55,
    "generating_documentation": 75,
    "persisting": 90,
    "complete": 100,
}


class JobSchema(BaseModel):
    schemaVersion: str = "1.0"
    jobId: str
    applicationSlug: str
    type: str  # onboarding | refresh
    status: str  # queued | running | succeeded | failed
    stage: str
    progressPercent: int
    message: str = ""
    attempt: int = 1
    error: str | None = None
    createdAt: str
    startedAt: str | None = None
    completedAt: str | None = None
    updatedAt: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        allowed = {"queued", "running", "succeeded", "failed"}
        if v not in allowed:
            raise ValueError(f"job status must be one of {allowed}")
        return v

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v: str) -> str:
        if v not in STAGE_PROGRESS:
            raise ValueError(f"stage must be one of {list(STAGE_PROGRESS)}")
        return v


# ---------------------------------------------------------------------------
# source_manifest.json
# ---------------------------------------------------------------------------
class SourceFileEntry(BaseModel):
    path: str
    language: str
    bytes: int
    sha256: str
    lineCount: int


class SourceManifestSchema(BaseModel):
    schemaVersion: str = "1.0"
    applicationSlug: str
    commitSha: str
    files: list[SourceFileEntry] = []


# ---------------------------------------------------------------------------
# graph.json
# ---------------------------------------------------------------------------
NODE_KINDS: frozenset[str] = frozenset(
    {
        "repository",
        "package",
        "module",
        "file",
        "class",
        "interface",
        "function",
        "method",
        "external_symbol",
    }
)

EDGE_KINDS: frozenset[str] = frozenset({"CONTAINS", "IMPORTS", "CALLS", "INHERITS", "IMPLEMENTS"})

RESOLUTION_VALUES: frozenset[str] = frozenset({"exact", "heuristic", "external", "unresolved"})


class GraphNode(BaseModel):
    id: str
    kind: str
    name: str
    qualifiedName: str
    language: str
    path: str
    startLine: int
    endLine: int
    signature: str = ""
    parentId: str | None = None
    attributes: dict[str, Any] = {}

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if v not in NODE_KINDS:
            raise ValueError(f"node kind must be one of {NODE_KINDS}")
        return v


class GraphEdge(BaseModel):
    id: str
    sourceId: str
    targetId: str
    kind: str
    resolution: str
    confidence: float
    path: str
    startLine: int
    endLine: int
    attributes: dict[str, Any] = {}

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if v not in EDGE_KINDS:
            raise ValueError(f"edge kind must be one of {EDGE_KINDS}")
        return v

    @field_validator("resolution")
    @classmethod
    def _valid_resolution(cls, v: str) -> str:
        if v not in RESOLUTION_VALUES:
            raise ValueError(f"resolution must be one of {RESOLUTION_VALUES}")
        return v


class GraphSchema(BaseModel):
    schemaVersion: str = "1.0"
    applicationSlug: str
    commitSha: str
    generatedAt: str
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    statistics: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# symbol_index.json
# ---------------------------------------------------------------------------
class SymbolIndexEntry(BaseModel):
    id: str
    name: str
    qualifiedName: str
    kind: str
    path: str
    startLine: int
    endLine: int
    signature: str = ""
    tokens: list[str] = []  # normalized search tokens


class SymbolIndexSchema(BaseModel):
    schemaVersion: str = "1.0"
    applicationSlug: str
    commitSha: str
    symbols: list[SymbolIndexEntry] = []


# ---------------------------------------------------------------------------
# documents.json
# ---------------------------------------------------------------------------
class DocumentEntry(BaseModel):
    slug: str
    title: str
    kind: str  # overview | module
    path: str
    moduleId: str | None = None
    sourceReferences: list[dict[str, Any]] = []


class DocumentsSchema(BaseModel):
    schemaVersion: str = "1.0"
    applicationSlug: str
    commitSha: str
    documents: list[DocumentEntry] = []
