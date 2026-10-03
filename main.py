"""Serve the DocWiki API at http://localhost:8000.

Run from the project root: python main.py
In a separate terminal: cd frontend; npm run dev
Open http://localhost:5173 for local development (no frontend build needed).

For production, npm run build in frontend creates dist, which this server
can also serve directly on port 8000.
"""

from __future__ import annotations

import os

import uvicorn

from ui_api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
