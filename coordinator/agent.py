"""Coordinator agent — root_agent for DocWiki.

Exports:
    root_agent — discoverable by ADK.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent

from coordinator import config
from coordinator.docgenerator.agent import doc_generator
from coordinator.tools.graph_tools import retrieve_context
from coordinator.tools.catalog_tools import (
    get_application_status,
    list_applications,
)
from coordinator.tools.onboarding_tools import onboard_repository

_PROMPT_PATH = (
    Path(__file__).parent
    / "prompts"
    / "coordinator_system.md"
)

_INSTRUCTION = (
    _PROMPT_PATH.read_text(encoding="utf-8")
    if _PROMPT_PATH.exists()
    else (
        "You are DocWiki's coordinator. "
        "Use tools to answer repository questions."
    )
)

root_agent = LlmAgent(
    name="coordinator",
    model=config.COORDINATOR_MODEL,
    instruction=_INSTRUCTION,
    description=(
        "DocWiki coordinator that onboards repositories "
        "and answers questions about selected repositories."
    ),
    tools=[
        # Homepage and onboarding tools
        list_applications,
        get_application_status,
        onboard_repository,

        # Graph-first repository retrieval; documentation is already in the UI.
        retrieve_context,
    ],
    sub_agents=[
        doc_generator,
    ],
)
