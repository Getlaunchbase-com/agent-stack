"""Append-only JSONL execution audit log.

Every estimate run appends a single line to /logs/estimate_runs.jsonl.
No database. No external service. Just a local file for:
  - Field debugging
  - Union defensibility
  - Determinism verification

Thread-safe via a module-level lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", "/logs"))
_LOG_FILE = _LOG_DIR / "estimate_runs.jsonl"
_lock = threading.Lock()


def _ensure_log_dir() -> bool:
    """Create the log directory if it doesn't exist. Returns True on success."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as e:
        logger.error("Cannot create audit log directory %s: %s", _LOG_DIR, e)
        return False


def log_estimate_run(
    *,
    project_id: str,
    document_id: str,
    estimate_total: float,
    confidence: float,
    model_version: str,
    tool_name: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a single audit record for an estimate run."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "document_id": document_id,
        "estimate_total": estimate_total,
        "confidence": confidence,
        "model_version": model_version,
        "tool_name": tool_name,
    }
    if extra:
        record["extra"] = extra

    line = json.dumps(record, separators=(",", ":"))

    with _lock:
        if not _ensure_log_dir():
            return
        try:
            with open(_LOG_FILE, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)


def log_tool_call(
    *,
    tool_name: str,
    ok: bool,
    error_code: str | None = None,
    duration_ms: float | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a general tool-call audit record."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "ok": ok,
    }
    if error_code:
        record["error_code"] = error_code
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 2)
    if extra:
        record["extra"] = extra

    line = json.dumps(record, separators=(",", ":"))

    with _lock:
        if not _ensure_log_dir():
            return
        try:
            with open(_LOG_FILE, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)
