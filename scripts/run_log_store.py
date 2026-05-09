from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .utils import read_json, utc_now_iso, write_json


def load_dedup_signatures_from_logs(logs_dir: str | Path) -> set[str]:
    logs_dir = Path(logs_dir)
    out: set[str] = set()
    if not logs_dir.exists():
        return out

    for p in logs_dir.glob("run-*.json"):
        data = read_json(p, default={})
        if not isinstance(data, dict):
            continue
        pushed = data.get("pushed_items", [])
        if not isinstance(pushed, list):
            continue
        for it in pushed:
            if isinstance(it, dict) and isinstance(it.get("sig"), str):
                out.add(it["sig"])
    return out


def write_run_log(logs_dir: str | Path, payload: dict) -> Path:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = utc_now_iso().replace(":", "-")
    run_path = logs_dir / f"run-{ts}.json"
    write_json(run_path, payload)
    write_json(logs_dir / "latest.json", payload)
    return run_path


def trim_old_logs(logs_dir: str | Path, keep: int = 120) -> None:
    logs_dir = Path(logs_dir)
    files = sorted(logs_dir.glob("run-*.json"))
    if len(files) <= keep:
        return
    for p in files[: len(files) - keep]:
        p.unlink(missing_ok=True)
