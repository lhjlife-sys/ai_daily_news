from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .utils import read_json, stable_hash, utc_now_iso, write_json


@dataclass
class SentState:
    version: int
    updated_at: str | None
    items: list[dict]


def load_state(path: str | Path) -> SentState:
    data = read_json(path, default={"version": 1, "updated_at": None, "items": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": None, "items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return SentState(version=int(data.get("version", 1)), updated_at=data.get("updated_at"), items=items)


def item_signature(url: str, title: str, published_at: str | None) -> str:
    base = (url or "").strip()
    if not base:
        base = f"{title}|{published_at or ''}"
    return stable_hash(base)


def existing_signatures(state: SentState) -> set[str]:
    out: set[str] = set()
    for it in state.items:
        if isinstance(it, dict) and isinstance(it.get("sig"), str):
            out.add(it["sig"])
    return out


def filter_new_items(items: Iterable[dict], state: SentState) -> list[dict]:
    seen = existing_signatures(state)
    out: list[dict] = []
    for it in items:
        sig = it.get("sig")
        if isinstance(sig, str) and sig and sig not in seen:
            out.append(it)
    return out


def mark_sent(state: SentState, sent_items: Iterable[dict], keep_days: int = 60) -> None:
    # Keep a compact log of sent signatures with timestamps.
    now = utc_now_iso()
    for it in sent_items:
        sig = it.get("sig")
        url = it.get("url")
        if not isinstance(sig, str) or not sig:
            continue
        state.items.append({"sig": sig, "url": url, "sent_at": now})

    # Best-effort trimming by count (~days window approximated).
    # Since GitHub Actions runs daily, keep_days ~= keep_days * max_selected (bounded).
    max_items = max(200, keep_days * 50)
    if len(state.items) > max_items:
        state.items = state.items[-max_items:]

    state.updated_at = now


def save_state(path: str | Path, state: SentState) -> None:
    write_json(path, {"version": state.version, "updated_at": state.updated_at, "items": state.items})
