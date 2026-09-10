"""main.py — local ADK testing entry point for DocWiki.

Usage:
    python main.py

This file is NOT the Agent Runtime deployment entry point.
The ADK deployment entry point is coordinator/agent.py (root_agent).

Supported ADK CLI commands (verified with google-adk 1.36.2):
    adk run coordinator     — run agent from CLI (non-interactive)
    adk web                 — launch the ADK development web UI
"""

from __future__ import annotations

# ruff: noqa: E402
import asyncio
import os

# Load .env before importing coordinator modules so env vars are in place.
from dotenv import load_dotenv

load_dotenv()

# Force local storage mode for local testing
os.environ.setdefault("DOCWIKI_STORAGE_MODE", "local")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from coordinator.agent import root_agent  # noqa: E402

_APP_NAME = "docwiki-local"
_USER_ID = "local-user"


async def _chat_loop() -> None:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        state={},
    )

    runner = Runner(
        agent=root_agent,
        app_name=_APP_NAME,
        session_service=session_service,
    )

    print(f"DocWiki local chat  (google-adk 1.36.2 · model: {root_agent.model})")
    print("Type 'quit' or press Ctrl-C / Ctrl-D to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye.")
            break

        if not user_input:
            continue

        message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_input)],
        )

        final_text: list[str] = []
        tool_calls: list[str] = []

        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session.id,
            new_message=message,
        ):
            # Collect tool calls for display
            if event.author != "coordinator" and event.content:
                for part in event.content.parts or []:
                    if hasattr(part, "function_call") and part.function_call:
                        tool_calls.append(f"  [tool] {part.function_call.name}()")
                    if hasattr(part, "function_response") and part.function_response:
                        tool_calls.append(f"  [tool response] {part.function_response.name}")

            # Final agent text
            if event.turn_complete and event.content:
                for part in event.content.parts or []:
                    if hasattr(part, "text") and part.text:
                        final_text.append(part.text)

        if tool_calls:
            for tc in tool_calls:
                print(tc)

        response = "".join(final_text).strip()
        if response:
            print(f"DocWiki: {response}\n")
        else:
            print("DocWiki: (no text response)\n")


if __name__ == "__main__":
    asyncio.run(_chat_loop())
