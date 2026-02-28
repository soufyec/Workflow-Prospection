import json
import os
from typing import Any

DEFAULT_STATE = {
    "discovered": [],
    "enriched": [],
    "generated": [],
    "review_file": None,
    "approved": [],
    "sent": [],
}


def load_state(path: str) -> dict:
    """Load pipeline state from JSON. Returns default state if missing or corrupt."""
    if not os.path.exists(path):
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_STATE.items()}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_STATE.items()}


def save_state(path: str, state: dict) -> None:
    """Atomically write state via .tmp file to avoid corruption on crash."""
    tmp_path = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def append_to_state_list(path: str, key: str, items: list) -> None:
    """Load state, append new items (deduped by website URL), and save."""
    state = load_state(path)
    existing_urls = {r.get("website", "") for r in state.get(key, [])}
    new_items = [i for i in items if i.get("website", "") not in existing_urls]
    state.setdefault(key, []).extend(new_items)
    save_state(path, state)
