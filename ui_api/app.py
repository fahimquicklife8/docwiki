"""DocWiki UI API.

FastAPI bridge between React and the ADK coordinator.

Run:

    python -m uvicorn ui_api.app:app --reload --port 8000
"""

# ruff: noqa: E402

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

os.environ.setdefault(
    "DOCWIKI_STORAGE_MODE",
    "local",
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from pydantic import BaseModel

from coordinator.agent import root_agent
from coordinator.schemas import is_valid_slug
from coordinator.tools.repo_store import get_store


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_APP_NAME = "docwiki"

_session_service = InMemorySessionService()

_runner = Runner(
    agent=root_agent,
    app_name=_APP_NAME,
    session_service=_session_service,
)

app = FastAPI(
    title="DocWiki UI API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


PageMode = Literal[
    "home",
    "repository",
]


class CreateSessionRequest(BaseModel):
    userId: str | None = None
    pageMode: PageMode = "home"
    applicationSlug: str | None = None


class SessionContextRequest(BaseModel):
    userId: str
    sessionId: str
    pageMode: PageMode
    applicationSlug: str | None = None


class ActivateRequest(BaseModel):
    userId: str
    sessionId: str


class ChatRequest(BaseModel):
    userId: str
    sessionId: str
    message: str
    pageMode: PageMode = "home"
    applicationSlug: str | None = None


# ---------------------------------------------------------------------------
# Session context helpers
# ---------------------------------------------------------------------------


async def _get_ready_application(
    slug: str,
) -> dict:
    if not is_valid_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="Invalid application slug",
        )

    store = get_store()

    try:
        metadata = await store.read_json(
            slug,
            "metadata.json",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Application not found",
        ) from None

    status = metadata.get("status")

    if status != "ready":
        raise HTTPException(
            status_code=400,
            detail=f"Application not ready: {status}",
        )

    return metadata


async def _build_context_state(
    page_mode: PageMode,
    application_slug: str | None,
) -> dict:
    if page_mode == "home":
        return {
            "page_mode": "home",
            "active_app": "",
            "active_commit_sha": "",
        }

    if not application_slug:
        raise HTTPException(
            status_code=400,
            detail=(
                "Repository mode requires "
                "an application slug"
            ),
        )

    metadata = await _get_ready_application(
        application_slug
    )

    return {
        "page_mode": "repository",
        "active_app": application_slug,
        "active_commit_sha": metadata.get(
            "commitSha",
            "",
        ),
    }


async def _apply_context_state(
    session,
    context_state: dict,
):
    already_current = all(
        session.state.get(key) == value
        for key, value in context_state.items()
    )

    if already_current:
        return session

    event = Event(
        invocation_id=(
            f"ui-context-{uuid.uuid4()}"
        ),
        author="ui",
        actions=EventActions(
            state_delta=context_state,
        ),
    )

    await _session_service.append_event(
        session=session,
        event=event,
    )

    updated_session = (
        await _session_service.get_session(
            app_name=_APP_NAME,
            user_id=session.user_id,
            session_id=session.id,
        )
    )

    if updated_session is None:
        raise HTTPException(
            status_code=500,
            detail="Session context update failed",
        )

    return updated_session


async def _get_session_or_404(
    user_id: str,
    session_id: str,
):
    session = await _session_service.get_session(
        app_name=_APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Chat session not found. "
                "Refresh the page."
            ),
        )

    return session


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session(
    body: CreateSessionRequest,
):
    user_id = (
        body.userId
        or str(uuid.uuid4())[:12]
    )

    initial_state = await _build_context_state(
        body.pageMode,
        body.applicationSlug,
    )

    session = await _session_service.create_session(
        app_name=_APP_NAME,
        user_id=user_id,
        state=initial_state,
    )

    return {
        "userId": user_id,
        "sessionId": session.id,
        "pageMode": initial_state["page_mode"],
        "activeApp": (
            initial_state["active_app"]
            or None
        ),
    }


@app.get(
    "/api/sessions/{user_id}/{session_id}"
)
async def verify_session(
    user_id: str,
    session_id: str,
):
    session = await _get_session_or_404(
        user_id,
        session_id,
    )

    return {
        "userId": user_id,
        "sessionId": session_id,
        "state": dict(session.state),
    }


@app.post("/api/sessions/context")
async def update_session_context(
    body: SessionContextRequest,
):
    session = await _get_session_or_404(
        body.userId,
        body.sessionId,
    )

    context_state = await _build_context_state(
        body.pageMode,
        body.applicationSlug,
    )

    updated_session = await _apply_context_state(
        session,
        context_state,
    )

    return {
        "ok": True,
        "pageMode": updated_session.state.get(
            "page_mode"
        ),
        "activeApp": (
            updated_session.state.get(
                "active_app"
            )
            or None
        ),
        "activeCommitSha": (
            updated_session.state.get(
                "active_commit_sha"
            )
            or None
        ),
    }


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


@app.get("/api/applications")
async def list_applications():
    store = get_store()

    try:
        catalog = await store.read_json(
            None,
            "list_apps.json",
        )

        return catalog.get(
            "applications",
            [],
        )

    except FileNotFoundError:
        slugs = (
            await store.list_application_slugs()
        )

        applications = []

        for slug in sorted(slugs):
            try:
                metadata = await store.read_json(
                    slug,
                    "metadata.json",
                )
            except FileNotFoundError:
                continue

            applications.append(
                {
                    "applicationSlug": slug,
                    "displayName": metadata.get(
                        "displayName",
                        slug,
                    ),
                    "repositoryUrl": metadata.get(
                        "repositoryUrl",
                        "",
                    ),
                    "branch": metadata.get(
                        "resolvedBranch",
                        "",
                    ),
                    "commitSha": metadata.get(
                        "commitSha",
                        "",
                    ),
                    "status": metadata.get(
                        "status",
                        "unknown",
                    ),
                    "languages": metadata.get(
                        "languages",
                        [],
                    ),
                    "updatedAt": metadata.get(
                        "updatedAt",
                        "",
                    ),
                }
            )

        return applications


@app.get("/api/applications/{slug}")
async def get_application(
    slug: str,
):
    if not is_valid_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="Invalid application slug",
        )

    store = get_store()

    try:
        return await store.read_json(
            slug,
            "metadata.json",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Application not found",
        ) from None


@app.get(
    "/api/applications/{slug}/documents"
)
async def list_documents(
    slug: str,
):
    if not is_valid_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="Invalid application slug",
        )

    store = get_store()

    try:
        documents = await store.read_json(
            slug,
            "documents.json",
        )

        return documents.get(
            "documents",
            [],
        )
    except FileNotFoundError:
        return []


@app.get(
    "/api/applications/{slug}/documents/{doc_slug}"
)
async def get_document(
    slug: str,
    doc_slug: str,
):
    if not is_valid_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="Invalid application slug",
        )

    store = get_store()

    try:
        documents = await store.read_json(
            slug,
            "documents.json",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Documents index not found",
        ) from None

    for document in documents.get(
        "documents",
        [],
    ):
        if document.get("slug") != doc_slug:
            continue

        document_path = document.get("path")

        if not document_path:
            raise HTTPException(
                status_code=500,
                detail="Document path is missing",
            )

        try:
            content = await store.read_text(
                slug,
                document_path,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="Document file not found",
            ) from None

        return {
            "document": document,
            "content": content,
        }

    raise HTTPException(
        status_code=404,
        detail="Document not found",
    )


# Compatibility endpoint for older frontend code.
@app.post(
    "/api/applications/{slug}/activate"
)
async def activate_application(
    slug: str,
    body: ActivateRequest,
):
    session = await _get_session_or_404(
        body.userId,
        body.sessionId,
    )

    context_state = await _build_context_state(
        "repository",
        slug,
    )

    updated_session = await _apply_context_state(
        session,
        context_state,
    )

    return {
        "ok": True,
        "activeApp": updated_session.state.get(
            "active_app"
        ),
        "activeCommitSha": (
            updated_session.state.get(
                "active_commit_sha"
            )
        ),
    }


# ---------------------------------------------------------------------------
# Chat streaming
# ---------------------------------------------------------------------------


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
):
    session = await _get_session_or_404(
        request.userId,
        request.sessionId,
    )

    # The frontend route is authoritative.
    # Reapply its context before every agent invocation.
    context_state = await _build_context_state(
        request.pageMode,
        request.applicationSlug,
    )

    session = await _apply_context_state(
        session,
        context_state,
    )

    logger.info(
        "Chat context: session=%s mode=%s active_app=%s",
        request.sessionId,
        session.state.get("page_mode"),
        session.state.get("active_app"),
    )

    async def generate():
        try:
            content = genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(
                        text=request.message
                    )
                ],
            )

            async for event in _runner.run_async(
                user_id=request.userId,
                session_id=request.sessionId,
                new_message=content,
            ):
                if (
                    not event.content
                    or not event.content.parts
                ):
                    continue

                for part in event.content.parts:
                    if (
                        getattr(
                            part,
                            "text",
                            None,
                        )
                    ):
                        payload = {
                            "type": "text",
                            "content": part.text,
                            "author": (
                                event.author
                                or "agent"
                            ),
                            "partial": bool(
                                getattr(
                                    event,
                                    "partial",
                                    False,
                                )
                            ),
                        }

                        yield (
                            "data: "
                            f"{json.dumps(payload)}"
                            "\n\n"
                        )

                    elif getattr(
                        part,
                        "function_call",
                        None,
                    ):
                        function_call = (
                            part.function_call
                        )

                        payload = {
                            "type": "tool_call",
                            "name": function_call.name,
                            "args": dict(
                                function_call.args
                                or {}
                            ),
                        }

                        yield (
                            "data: "
                            f"{json.dumps(payload)}"
                            "\n\n"
                        )

                    elif getattr(
                        part,
                        "function_response",
                        None,
                    ):
                        function_response = (
                            part.function_response
                        )

                        response = (
                            function_response.response
                            or {}
                        )

                        result = (
                            dict(response)
                            if isinstance(
                                response,
                                dict,
                            )
                            else {
                                "value": str(response)
                            }
                        )

                        payload = {
                            "type": "tool_result",
                            "name": (
                                function_response.name
                            ),
                            "result": result,
                        }

                        yield (
                            "data: "
                            f"{json.dumps(payload)}"
                            "\n\n"
                        )

        except Exception:
            logger.exception(
                "Chat stream error for session %s",
                request.sessionId,
            )

            payload = {
                "type": "error",
                "message": (
                    "An error occurred while "
                    "contacting the agent."
                ),
            }

            yield (
                "data: "
                f"{json.dumps(payload)}"
                "\n\n"
            )

        yield (
            "data: "
            f"{json.dumps({'type': 'done'})}"
            "\n\n"
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )