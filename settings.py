"""
User-configurable runtime settings for RAG.
Stored in settings.json next to app.py — persists across restarts.

Settings (keys):
    llm_model   — Active Ollama model name (overrides config.LLM_MODEL)
    data_dir    — Data storage root path (requires restart to take effect)
"""

import json
import logging
from pathlib import Path
from typing import Any

SETTINGS_FILE = Path(__file__).parent / "settings.json"

logger = logging.getLogger(__name__)


def load_settings() -> dict:
    """Load all settings from settings.json. Returns {} if file missing or corrupt."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        content = SETTINGS_FILE.read_text(encoding="utf-8")
        return json.loads(content) if content.strip() else {}
    except Exception as e:
        logger.warning(f"[SETTINGS] Error loading: {e}")
        return {}


def get_setting(key: str, default: Any = None) -> Any:
    """Get a single setting value, falling back to default if not set."""
    return load_settings().get(key, default)


def save_setting(key: str, value: Any) -> None:
    """Update a single setting and persist to settings.json."""
    settings = load_settings()
    settings[key] = value
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    logger.info(f"[SETTINGS] Saved: {key} = {value}")


def save_settings(updates: dict) -> None:
    """Update multiple settings at once and persist."""
    settings = load_settings()
    settings.update(updates)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    logger.info(f"[SETTINGS] Saved: {list(updates.keys())}")
