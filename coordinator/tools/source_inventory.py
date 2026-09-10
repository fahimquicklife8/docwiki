"""Source file inventory — discovers Python and Java files within an extracted repo."""

from __future__ import annotations

import hashlib
from pathlib import Path

SUPPORTED_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".java": "java",
}

# Directories to skip during inventory
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".tox",
        "venv",
        ".venv",
        "env",
        "build",
        "dist",
        "target",
        ".gradle",
    }
)


def inventory_source_files(
    repo_root: str,
    max_files: int,
    max_total_bytes: int,
    max_single_bytes: int,
) -> list[dict]:
    """Walk repo_root and return metadata for all supported source files.

    Each entry: {path, language, bytes, sha256, lineCount}.
    Paths are relative to repo_root using forward slashes.

    Raises ValueError("REPOSITORY_TOO_LARGE") if limits are exceeded.
    Raises ValueError("NO_SUPPORTED_SOURCE") if no supported files found.
    """
    root = Path(repo_root)
    files: list[dict] = []
    total_bytes = 0

    for path in sorted(root.rglob("*")):
        # Skip unwanted directories
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue

        ext = path.suffix.lower()
        language = SUPPORTED_LANGUAGES.get(ext)
        if language is None:
            continue

        size = path.stat().st_size
        if size > max_single_bytes:
            continue  # silently skip oversized individual files

        total_bytes += size
        if total_bytes > max_total_bytes:
            raise ValueError("REPOSITORY_TOO_LARGE")
        if len(files) >= max_files:
            raise ValueError("REPOSITORY_TOO_LARGE")

        raw = path.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()

        try:
            text = raw.decode("utf-8", errors="replace")
            line_count = len(text.splitlines()) or 1
        except Exception:
            line_count = 1

        rel = path.relative_to(root).as_posix()
        files.append(
            {
                "path": rel,
                "language": language,
                "bytes": size,
                "sha256": sha256,
                "lineCount": line_count,
            }
        )

    if not files:
        raise ValueError("NO_SUPPORTED_SOURCE")

    return files
