"""Pydantic schemas for documentation sub-agent messages."""

from __future__ import annotations

from pydantic import BaseModel


class DocGenerationRequest(BaseModel):
    applicationSlug: str
    commitSha: str


class DocGenerationResult(BaseModel):
    ok: bool
    applicationSlug: str
    documentsWritten: int = 0
    error: str | None = None
