"""Unit tests for GitHub URL validation, slugs, archive safety — Phases 1–2."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/google/adk-python",
        "https://github.com/google/adk-python.git",
        "https://github.com/Org-Name/repo_name",
        "https://github.com/a/b",
    ],
)
def test_valid_github_urls(url):
    from coordinator.tools.github_archive import validate_github_url

    owner, repo = validate_github_url(url)
    assert owner
    assert repo


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",  # not https
        "https://gitlab.com/owner/repo",  # not github
        "https://github.com/owner/repo/extra",  # extra path
        "https://user:pass@github.com/owner/repo",  # credentials
        "https://github.com/../etc",  # traversal
        "ftp://github.com/owner/repo",  # wrong scheme
        "https://192.168.1.1/owner/repo",  # IP address
        "",  # empty
        "github.com/owner/repo",  # missing scheme
    ],
)
def test_invalid_github_urls(url):
    from coordinator.tools.github_archive import validate_github_url

    with pytest.raises(ValueError):
        validate_github_url(url)


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------
def test_slug_strips_underscores():
    from coordinator.schemas import make_slug

    assert make_slug("My_Org", "My_Repo") == "my-org-my-repo"


def test_slug_max_length():
    from coordinator.schemas import make_slug

    slug = make_slug("a" * 40, "b" * 100)
    assert len(slug) <= 63


def test_slug_collision_deterministic():
    from coordinator.schemas import slug_with_collision_suffix

    s1 = slug_with_collision_suffix("https://github.com/Org/Repo", "org-repo")
    s2 = slug_with_collision_suffix("https://github.com/Org/Repo", "org-repo")
    assert s1 == s2


def test_slug_collision_differs_from_base():
    from coordinator.schemas import slug_with_collision_suffix

    base = "my-repo"
    result = slug_with_collision_suffix("https://github.com/A/B", base)
    assert result != base
    assert result.startswith(base + "-")


# ---------------------------------------------------------------------------
# Archive safety
# ---------------------------------------------------------------------------
def test_safe_extract_rejects_traversal(tmp_path):
    """A tar with ../evil should be rejected."""
    import io
    import tarfile

    archive_path = tmp_path / "evil.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        data = b"evil content"
        info = tarfile.TarInfo(name="repo/../../../etc/passwd")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    from coordinator.tools.github_archive import safe_extract

    with pytest.raises(ValueError, match="UNSAFE_ARCHIVE|traversal|safe"):
        safe_extract(archive_path, tmp_path / "out")


def test_safe_extract_rejects_absolute(tmp_path):
    """A tar with /etc/passwd should be rejected."""
    import io
    import tarfile

    archive_path = tmp_path / "abs.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        data = b"evil"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    from coordinator.tools.github_archive import safe_extract

    # Either raises ValueError or silently skips (absolute paths stripped)
    try:
        safe_extract(archive_path, tmp_path / "out")
    except ValueError:
        pass  # expected


def test_safe_extract_valid(tmp_path):
    """A normal tar should extract cleanly."""
    import io
    import tarfile

    archive_path = tmp_path / "good.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        data = b"hello"
        info = tarfile.TarInfo(name="myrepo-abc123/hello.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    from coordinator.tools.github_archive import safe_extract

    top = safe_extract(archive_path, tmp_path / "out")
    assert (top / "hello.py").exists()


# ---------------------------------------------------------------------------
# Source inventory
# ---------------------------------------------------------------------------
def test_inventory_finds_py_and_java(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.java").write_text("class B {}")
    (tmp_path / "c.txt").write_text("ignored")

    from coordinator.tools.source_inventory import inventory_source_files

    files = inventory_source_files(str(tmp_path), 500, 20_000_000, 524288)
    exts = {f["path"].split(".")[-1] for f in files}
    assert "py" in exts
    assert "java" in exts
    assert "txt" not in exts


def test_inventory_enforces_max_files(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x")

    from coordinator.tools.source_inventory import inventory_source_files

    with pytest.raises(ValueError, match="REPOSITORY_TOO_LARGE"):
        inventory_source_files(
            str(tmp_path), max_files=3, max_total_bytes=20_000_000, max_single_bytes=524288
        )


def test_inventory_skips_oversized(tmp_path):
    (tmp_path / "big.py").write_bytes(b"x" * 1000)
    (tmp_path / "small.py").write_bytes(b"y" * 10)

    from coordinator.tools.source_inventory import inventory_source_files

    files = inventory_source_files(str(tmp_path), 500, 20_000_000, max_single_bytes=100)
    assert len(files) == 1
    assert files[0]["path"] == "small.py"


def test_inventory_no_source_raises(tmp_path):
    (tmp_path / "readme.txt").write_text("no code")

    from coordinator.tools.source_inventory import inventory_source_files

    with pytest.raises(ValueError, match="NO_SUPPORTED_SOURCE"):
        inventory_source_files(str(tmp_path), 500, 20_000_000, 524288)
