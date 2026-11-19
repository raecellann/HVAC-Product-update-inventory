"""Local dev entry point: python run_web.py

Serves on http://127.0.0.1:8000. Set ACCESS_KEY in .env and visit
http://127.0.0.1:8000/?key=<your key> on first load.
"""
from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "webapp.app:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
        reload=False,
    )
