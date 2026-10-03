"""Serve the DocWiki frontend and chat API together at http://localhost:8000.

Build the frontend first: cd frontend; npm run build
Then run from the project root: python main.py
Alternatively, run npm run dev from frontend to build and start both.
"""

from __future__ import annotations

import os

import uvicorn

from ui_api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
