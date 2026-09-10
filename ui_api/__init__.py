"""DocWiki UI API — FastAPI bridge between the React frontend and the ADK coordinator.

Run:
    python -m uvicorn ui_api.app:app --reload --port 8000

The frontend (Vite dev server on port 5173) talks only to this server.
No separate `adk web` process is needed.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path

# Make coordinator importable when running from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
os.environ.setdefault("DOCWIKI_STORAGE_MODE", "local")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

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

app = FastAPI(title="DocWiki UI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session():
    """Create a new ADK session and return userId + sessionId."""
    user_id = str(uuid.uuid4())[:12]
    session = await _session_service.create_session(
        app_name=_APP_NAME, user_id=user_id, state={}
    )
    return {"userId": user_id, "sessionId": session.id}


@app.get("/api/sessions/{user_id}/{session_id}")
async def verify_session(user_id: str, session_id: str):
    """Check that a session still exists in memory."""
    session = await _session_service.get_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"userId": user_id, "sessionId": session_id, "state": dict(session.state)}


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


@app.get("/api/applications")
async def list_applications():
    """Return all onboarded applications from the catalog."""
    store = get_store()
    try:
        catalog = await store.read_json(None, "list_apps.json")
        return catalog.get("applications", [])
    except FileNotFoundError:
        slugs = await store.list_application_slugs()
        apps = []
        for slug in sorted(slugs):
            try:
                meta = await store.read_json(slug, "metadata.json")
                apps.append(
                    {
                        "applicationSlug": slug,
                        "displayName": meta.get("displayName", slug),
                        "repositoryUrl": meta.get("repositoryUrl", ""),
                        "branch": meta.get("resolvedBranch", ""),
                        "commitSha": meta.get("commitSha", ""),
                        "status": meta.get("status", "unknown"),
                        "languages": meta.get("languages", []),
                        "updatedAt": meta.get("updatedAt", ""),
                    }
                )
            except FileNotFoundError:
                pass
        return apps


@app.get("/api/applications/{slug}")
async def get_application(slug: str):
    if not is_valid_slug(slug):
        raise HTTPException(400, "Invalid application slug")
    store = get_store()
    try:
        return await store.read_json(slug, "metadata.json")
    except FileNotFoundError:
        raise HTTPException(404, "Application not found")


@app.get("/api/applications/{slug}/documents")
async def list_documents(slug: str):
    if not is_valid_slug(slug):
        raise HTTPException(400, "Invalid application slug")
    store = get_store()
    try:
        docs = await store.read_json(slug, "documents.json")
        return docs.get("documents", [])
    except FileNotFoundError:
        return []


@app.get("/api/applications/{slug}/documents/{doc_slug}")
async def get_document(slug: str, doc_slug: str):
    if not is_valid_slug(slug):
        raise HTTPException(400, "Invalid application slug")
    store = get_store()
    try:
        docs = await store.read_json(slug, "documents.json")
    except FileNotFoundError:
        raise HTTPException(404, "Documents index not found")
    for doc in docs.get("documents", []):
        if doc.get("slug") == doc_slug:
            try:
                content = await store.read_text(slug, doc["path"])
                return {"document": doc, "content": content}
            except FileNotFoundError:
                raise HTTPException(404, "Document file not found")
    raise HTTPException(404, "Document not found")


class ActivateRequest(BaseModel):
    userId: str
    sessionId: str


@app.post("/api/applications/{slug}/activate")
async def activate_application(slug: str, body: ActivateRequest):
    """Set active_app in the ADK session directly — no LLM call needed."""
    if not is_valid_slug(slug):
        raise HTTPException(400, "Invalid application slug")
    store = get_store()
    try:
        meta = await store.read_json(slug, "metadata.json")
    except FileNotFoundError:
        raise HTTPException(404, "Application not found")
    if meta.get("status") != "ready":
        raise HTTPException(400, f"Application not ready: {meta.get('status')}")

    session = await _session_service.get_session(
        app_name=_APP_NAME, user_id=body.userId, session_id=body.sessionId
    )
    if not session:
        session = await _session_service.create_session(
            app_name=_APP_NAME,
            user_id=body.userId,
            session_id=body.sessionId,
            state={},
        )

    session.state["active_app"] = slug
    session.state["active_commit_sha"] = meta.get("commitSha", "")

    return {
        "ok": True,
        "activeApp": slug,
        "commitSha": meta.get("commitSha", ""),
        "displayName": meta.get("displayName", slug),
    }


# ---------------------------------------------------------------------------
# Chat streaming (SSE)
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    userId: str
    sessionId: str
    message: str


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Send a message to the coordinator and stream events back as SSE."""
    session = await _session_service.get_session(
        app_name=_APP_NAME, user_id=request.userId, session_id=request.sessionId
    )
    if not session:
        session = await _session_service.create_session(
            app_name=_APP_NAME,
            user_id=request.userId,
            session_id=request.sessionId,
            state={},
        )

    async def generate():
        try:
            content = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=request.message)],
            )
            async for event in _runner.run_async(
                user_id=request.userId,
                session_id=request.sessionId,
                new_message=content,
            ):
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        payload = {
                            "type": "text",
                            "content": part.text,
                            "author": event.author or "agent",
                            "partial": bool(getattr(event, "partial", False)),
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                    elif hasattr(part, "function_call") and part.function_call:
                        payload = {
                            "type": "tool_call",
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args or {}),
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                    elif hasattr(part, "function_response") and part.function_response:
                        resp = part.function_response.response or {}
                        payload = {
                            "type": "tool_result",
                            "name": part.function_response.name,
                            "result": dict(resp) if isinstance(resp, dict) else {"value": str(resp)},
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
        except Exception:
            logger.exception("Chat stream error for session %s", request.sessionId)
            yield f"data: {json.dumps({'type': 'error', 'message': 'An error occurred.'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
