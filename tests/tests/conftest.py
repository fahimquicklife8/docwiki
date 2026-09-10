"""Shared pytest fixtures for DocWiki tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Force local storage mode in all tests
os.environ["DOCWIKI_STORAGE_MODE"] = "local"


@pytest.fixture()
def tmp_repos(tmp_path: Path):
    """A temporary repos root for LocalRepoStore tests."""
    repos = tmp_path / "repos"
    repos.mkdir()
    return repos
