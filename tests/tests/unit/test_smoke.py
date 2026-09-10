"""Phase 0 — smoke tests.

Gate requirements:
 - Imports succeed
 - root_agent is discoverable
 - doc_generator is discoverable as a sub-agent
 - Local store creates fixed artifact paths
 - Schemas validate correctly
 - Slug generation is correct
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("DOCWIKI_STORAGE_MODE", "local")


# ---------------------------------------------------------------------------
# 1. Import smoke tests
# ---------------------------------------------------------------------------


def test_coordinator_agent_imports():
    from coordinator.agent import root_agent  # noqa: F401

    assert root_agent is not None
    assert root_agent.name == "coordinator"


def test_root_agent_has_doc_generator_subagent():
    from coordinator.agent import root_agent

    sub_names = [a.name for a in (root_agent.sub_agents or [])]
    assert "doc_generator" in sub_names, f"sub_agents: {sub_names}"


def test_docgenerator_agent_imports():
    from coordinator.docgenerator.agent import doc_generator  # noqa: F401

    assert doc_generator is not None
    assert doc_generator.name == "doc_generator"


def test_config_imports():
    from coordinator import config  # noqa: F401

    assert config.COORDINATOR_MODEL
    assert config.MAX_SOURCE_FILES > 0


def test_schemas_import():
    from coordinator import schemas  # noqa: F401

    assert schemas.ALLOWED_APP_JSON_FILENAMES


def test_repo_store_imports():
    from coordinator.tools.repo_store import LocalRepoStore, get_store  # noqa: F401


def test_parser_registry_imports_and_smoke():
    """Grammar smoke test — Tree-sitter loads Python and Java grammars."""
    from coordinator.tools.parser_registry import get_parser, supported_languages

    langs = supported_languages()
    assert "python" in langs
    assert "java" in langs

    py_parser = get_parser("python")
    tree = py_parser.parse(b"def hello(): pass")
    assert tree.root_node.type == "module"

    java_parser = get_parser("java")
    tree = java_parser.parse(b"class Foo { void bar() {} }")
    assert tree.root_node.type == "program"


def test_all_tool_modules_import():
    import coordinator.tools.analyzer_base  # noqa: F401
    import coordinator.tools.catalog_tools  # noqa: F401
    import coordinator.tools.documentation_tools  # noqa: F401
    import coordinator.tools.github_archive  # noqa: F401
    import coordinator.tools.graph_builder  # noqa: F401
    import coordinator.tools.graph_tools  # noqa: F401
    import coordinator.tools.java_analyzer  # noqa: F401
    import coordinator.tools.onboarding_tools  # noqa: F401
    import coordinator.tools.parser_registry  # noqa: F401
    import coordinator.tools.python_analyzer  # noqa: F401
    import coordinator.tools.source_inventory  # noqa: F401
    import coordinator.tools.source_tools  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Slug generation
# ---------------------------------------------------------------------------


def test_slug_basic():
    from coordinator.schemas import make_slug

    assert make_slug("ExampleOrg", "Payment_API") == "exampleorg-payment-api"


def test_slug_lowercase():
    from coordinator.schemas import make_slug

    assert make_slug("MyOrg", "MyRepo") == "myorg-myrepo"


def test_slug_valid_pattern():
    from coordinator.schemas import is_valid_slug, make_slug

    slug = make_slug("ExampleOrg", "Payment_API")
    assert is_valid_slug(slug)


def test_slug_collision_suffix():
    from coordinator.schemas import slug_with_collision_suffix

    base = "exampleorg-payment-api"
    result = slug_with_collision_suffix("https://github.com/ExampleOrg/Payment_API", base)
    assert result.startswith(base + "-")
    assert len(result) <= 63


def test_is_valid_slug_rejects_bad():
    from coordinator.schemas import is_valid_slug

    assert not is_valid_slug("")
    assert not is_valid_slug("-startswithdash")
    assert not is_valid_slug("has/slash")
    assert not is_valid_slug("HAS_UPPER")


def test_is_valid_slug_accepts_good():
    from coordinator.schemas import is_valid_slug

    assert is_valid_slug("a")
    assert is_valid_slug("my-repo-123")
    assert is_valid_slug("exampleorg-payment-api")


# ---------------------------------------------------------------------------
# 3. Schema validation
# ---------------------------------------------------------------------------


def test_metadata_schema_valid():
    from coordinator.schemas import MetadataSchema

    m = MetadataSchema(
        applicationSlug="my-repo",
        displayName="My Repo",
        repositoryOwner="Org",
        repositoryName="repo",
        repositoryUrl="https://github.com/Org/repo",
        status="ready",
        createdAt="2026-07-22T00:00:00Z",
        updatedAt="2026-07-22T00:00:00Z",
    )
    assert m.status == "ready"


def test_metadata_schema_rejects_bad_status():
    from pydantic import ValidationError

    from coordinator.schemas import MetadataSchema

    with pytest.raises(ValidationError):
        MetadataSchema(
            applicationSlug="x",
            displayName="x",
            repositoryOwner="x",
            repositoryName="x",
            repositoryUrl="https://github.com/x/x",
            status="invalid_status",
            createdAt="2026-07-22T00:00:00Z",
            updatedAt="2026-07-22T00:00:00Z",
        )


def test_job_schema_valid():
    from coordinator.schemas import JobSchema

    j = JobSchema(
        jobId="abc-123",
        applicationSlug="my-repo",
        type="onboarding",
        status="running",
        stage="downloading",
        progressPercent=15,
        createdAt="2026-07-22T00:00:00Z",
        updatedAt="2026-07-22T00:00:00Z",
    )
    assert j.progressPercent == 15


def test_job_schema_rejects_bad_stage():
    from pydantic import ValidationError

    from coordinator.schemas import JobSchema

    with pytest.raises(ValidationError):
        JobSchema(
            jobId="x",
            applicationSlug="x",
            type="onboarding",
            status="running",
            stage="not_a_real_stage",
            progressPercent=0,
            createdAt="2026-07-22T00:00:00Z",
            updatedAt="2026-07-22T00:00:00Z",
        )


def test_graph_node_rejects_bad_kind():
    from pydantic import ValidationError

    from coordinator.schemas import GraphNode

    with pytest.raises(ValidationError):
        GraphNode(
            id="x",
            kind="not_a_kind",
            name="x",
            qualifiedName="x",
            language="python",
            path="x.py",
            startLine=1,
            endLine=1,
        )


def test_graph_edge_rejects_bad_resolution():
    from pydantic import ValidationError

    from coordinator.schemas import GraphEdge

    with pytest.raises(ValidationError):
        GraphEdge(
            id="e1",
            sourceId="a",
            targetId="b",
            kind="CALLS",
            resolution="invented",
            confidence=1.0,
            path="x.py",
            startLine=1,
            endLine=1,
        )


def test_stage_progress_map_complete():
    from coordinator.schemas import STAGE_PROGRESS

    required = {
        "validating",
        "downloading",
        "inventory",
        "parsing",
        "building_graph",
        "generating_documentation",
        "persisting",
        "complete",
    }
    assert required == set(STAGE_PROGRESS.keys())


def test_allowed_filenames_complete():
    from coordinator.schemas import ALLOWED_APP_JSON_FILENAMES

    required = {
        "metadata.json",
        "job.json",
        "graph.json",
        "symbol_index.json",
        "source_manifest.json",
        "documents.json",
    }
    assert required == ALLOWED_APP_JSON_FILENAMES


# ---------------------------------------------------------------------------
# 4. LocalRepoStore — fixed artifact paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_store_write_and_read_catalog(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    catalog = {"schemaVersion": "1.0", "updatedAt": "2026-07-22T00:00:00Z", "applications": []}
    await store.write_json(None, "list_apps.json", catalog)
    result = await store.read_json(None, "list_apps.json")
    assert result["schemaVersion"] == "1.0"


@pytest.mark.asyncio
async def test_local_store_write_and_read_app_json(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    meta = {"schemaVersion": "1.0", "applicationSlug": "my-app"}
    await store.write_json("my-app", "metadata.json", meta)
    result = await store.read_json("my-app", "metadata.json")
    assert result["applicationSlug"] == "my-app"


@pytest.mark.asyncio
async def test_local_store_rejects_disallowed_filename(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    with pytest.raises(ValueError, match="Disallowed"):
        await store.write_json("my-app", "my_graph.json", {})


@pytest.mark.asyncio
async def test_local_store_rejects_path_traversal_artifact(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    with pytest.raises(ValueError):
        await store.write_text("my-app", "source/../../../etc/passwd", "evil")


@pytest.mark.asyncio
async def test_local_store_rejects_non_source_docs_prefix(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    with pytest.raises(ValueError):
        await store.write_text("my-app", "metadata.json", "not allowed")


@pytest.mark.asyncio
async def test_local_store_write_and_read_source_file(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_text("my-app", "source/foo/bar.py", "def hello(): pass\n")
    content = await store.read_text("my-app", "source/foo/bar.py")
    assert "hello" in content


@pytest.mark.asyncio
async def test_local_store_list_slugs(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    # No metadata.json yet → no slugs
    slugs = await store.list_application_slugs()
    assert slugs == []

    # Write metadata for two apps
    await store.write_json("app-one", "metadata.json", {"ok": True})
    await store.write_json("app-two", "metadata.json", {"ok": True})

    slugs = await store.list_application_slugs()
    assert set(slugs) == {"app-one", "app-two"}


@pytest.mark.asyncio
async def test_local_store_list_paths(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    await store.write_text("my-app", "source/a.py", "# a")
    await store.write_text("my-app", "source/b.py", "# b")
    paths = await store.list_paths("my-app", "source")
    assert "source/a.py" in paths
    assert "source/b.py" in paths


@pytest.mark.asyncio
async def test_local_store_read_missing_raises(tmp_repos):
    from coordinator.tools.repo_store import LocalRepoStore

    store = LocalRepoStore(root=tmp_repos)
    with pytest.raises(FileNotFoundError):
        await store.read_json("no-such-app", "metadata.json")


# ---------------------------------------------------------------------------
# 5. GcsRepoStore raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gcs_store_raises_without_credentials():
    """GcsRepoStore is implemented but requires valid GCP credentials."""
    from coordinator.tools.repo_store import GcsRepoStore

    store = GcsRepoStore(bucket="my-bucket")
    # It will raise google.auth errors or similar — not NotImplementedError
    with pytest.raises(Exception):
        await store.read_json(None, "list_apps.json")


# ---------------------------------------------------------------------------
# 6. root_agent tool count sanity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_agent_has_expected_tools():
    from coordinator.agent import root_agent

    tool_names = {t.name for t in await root_agent.canonical_tools()}
    expected = {
        "list_applications",
        "get_application_status",
        "select_application",
        "onboard_repository",
        "get_repository_overview",
        "list_documents",
        "get_document",
        "search_symbols",
        "get_symbol",
        "get_graph_neighbors",
        "read_source",
        "search_source",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


# ---------------------------------------------------------------------------
# 7. Example-application fixture JSON files validate against schemas
# ---------------------------------------------------------------------------


def _fixture_path(filename: str) -> Path:
    return (
        Path(__file__).parent.parent.parent
        / "coordinator"
        / "repos"
        / "example-application"
        / filename
    )


def test_example_metadata_json_is_valid():
    from coordinator.schemas import MetadataSchema

    data = json.loads(_fixture_path("metadata.json").read_text())
    MetadataSchema(**data)


def test_example_job_json_is_valid():
    from coordinator.schemas import JobSchema

    data = json.loads(_fixture_path("job.json").read_text())
    JobSchema(**data)


def test_example_graph_json_is_valid():
    from coordinator.schemas import GraphSchema

    data = json.loads(_fixture_path("graph.json").read_text())
    GraphSchema(**data)


def test_example_source_manifest_is_valid():
    from coordinator.schemas import SourceManifestSchema

    data = json.loads(_fixture_path("source_manifest.json").read_text())
    SourceManifestSchema(**data)


def test_example_documents_json_is_valid():
    from coordinator.schemas import DocumentsSchema

    data = json.loads(_fixture_path("documents.json").read_text())
    DocumentsSchema(**data)


def test_example_symbol_index_is_valid():
    from coordinator.schemas import SymbolIndexSchema

    data = json.loads(_fixture_path("symbol_index.json").read_text())
    SymbolIndexSchema(**data)
