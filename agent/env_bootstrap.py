"""Load optional `.env` from project root so CLI and API pick up Ollama/OpenAI settings."""

from __future__ import annotations

from pathlib import Path


def load_app_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
