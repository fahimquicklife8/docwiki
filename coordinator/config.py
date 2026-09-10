"""DocWiki configuration — reads environment variables (loaded from .env by caller)."""

from __future__ import annotations

import os
import tempfile as _tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime environment
# ---------------------------------------------------------------------------
DOCWIKI_ENV: str = os.environ.get("DOCWIKI_ENV", "local")
DOCWIKI_STORAGE_MODE: str = os.environ.get("DOCWIKI_STORAGE_MODE", "local")
DOCWIKI_BUCKET: str = os.environ.get("DOCWIKI_BUCKET", "")
DOCWIKI_GCS_PREFIX: str = os.environ.get("DOCWIKI_GCS_PREFIX", "repos")

# ---------------------------------------------------------------------------
# Google Cloud / Gemini
# ---------------------------------------------------------------------------
GOOGLE_CLOUD_PROJECT: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION: str = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
COORDINATOR_MODEL: str = os.environ.get("COORDINATOR_MODEL", "gemini-2.0-flash")
DOCUMENTATION_MODEL: str = os.environ.get("DOCUMENTATION_MODEL", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# Repository / graph limits
# ---------------------------------------------------------------------------
MAX_SOURCE_FILES: int = int(os.environ.get("MAX_SOURCE_FILES", "500"))
MAX_TOTAL_SOURCE_BYTES: int = int(os.environ.get("MAX_TOTAL_SOURCE_BYTES", "20971520"))
MAX_SINGLE_FILE_BYTES: int = int(os.environ.get("MAX_SINGLE_FILE_BYTES", "524288"))
MAX_GRAPH_NODES: int = int(os.environ.get("MAX_GRAPH_NODES", "15000"))
MAX_GRAPH_EDGES: int = int(os.environ.get("MAX_GRAPH_EDGES", "50000"))
MAX_DOC_MODULES: int = int(os.environ.get("MAX_DOC_MODULES", "5"))
MAX_CONTEXT_CHARACTERS: int = int(os.environ.get("MAX_CONTEXT_CHARACTERS", "100000"))
MAX_SOURCE_TOOL_LINES: int = int(os.environ.get("MAX_SOURCE_TOOL_LINES", "300"))
MAX_GRAPH_NEIGHBOR_NODES: int = int(os.environ.get("MAX_GRAPH_NEIGHBOR_NODES", "150"))
# Approximate source bytes per module chunk for documentation generation (~100 KB)
MODULE_CHUNK_BYTES: int = int(os.environ.get("MODULE_CHUNK_BYTES", "102400"))
# Max characters of source loaded into a single doc-context tool response
DOC_CONTEXT_CHARS: int = int(os.environ.get("DOC_CONTEXT_CHARS", "24000"))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TEMP_ROOT: Path = Path(os.environ.get("TEMP_ROOT", str(Path(_tempfile.gettempdir()) / "docwiki")))

# Root of this package — used by LocalRepoStore
_PACKAGE_DIR: Path = Path(__file__).parent
LOCAL_REPOS_ROOT: Path = _PACKAGE_DIR / "repos"
