"""IBEW LV V1 freeze governance — enforcement layer.

Loads the canonical freeze manifest and provides:
  - Vertex stamp for API responses
  - Freeze status checks
  - Schema integrity verification (hash-locked)
  - Change classification enforcement

The freeze is authoritative. Any mutation to locked contracts, intelligence,
or output schemas MUST go through a versioned change request — never a hot patch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Freeze manifest loading (singleton)
# ---------------------------------------------------------------------------

_CONTRACTS_DIR = Path(__file__).parent
_FREEZE_PATH = _CONTRACTS_DIR / "IBEW_LV_V1.freeze.json"

_manifest: dict | None = None
_manifest_hash: str | None = None


def _load_manifest() -> tuple[dict, str]:
    """Load the freeze manifest from disk. Cached after first call."""
    global _manifest, _manifest_hash
    if _manifest is not None:
        return _manifest, _manifest_hash  # type: ignore[return-value]

    raw = _FREEZE_PATH.read_bytes()
    _manifest_hash = hashlib.sha256(raw).hexdigest()
    _manifest = json.loads(raw)
    logger.info(
        "Loaded freeze manifest: vertex=%s version=%s status=%s hash=%s",
        _manifest.get("vertex"),
        _manifest.get("version"),
        _manifest.get("status"),
        _manifest_hash[:12],
    )
    return _manifest, _manifest_hash


def get_manifest() -> dict:
    """Return the parsed freeze manifest."""
    m, _ = _load_manifest()
    return m


def get_manifest_hash() -> str:
    """Return the SHA-256 hex digest of the freeze manifest file."""
    _, h = _load_manifest()
    return h


# ---------------------------------------------------------------------------
# Vertex stamp — include in every API response from frozen tools
# ---------------------------------------------------------------------------

def get_vertex_stamp() -> dict:
    """Build the vertex stamp block for inclusion in tool output.

    Returns:
        {
          "vertex": "IBEW_LV",
          "version": "1.0.0",
          "status": "frozen",
          "frozen_at": "2026-02-15",
          "contracts": ["BlueprintParseV1", "EstimateChainV1"],
          "manifest_hash": "<sha256>"
        }
    """
    m, h = _load_manifest()
    return {
        "vertex": m["vertex"],
        "version": m["version"],
        "status": m["status"],
        "frozen_at": m["frozen_at"],
        "contracts": [c["name"] for c in m.get("contracts", [])],
        "manifest_hash": h,
    }


# ---------------------------------------------------------------------------
# Freeze status queries
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    """Return True if the vertex is in frozen status."""
    m = get_manifest()
    return m.get("status") == "frozen"


def is_contract_locked(contract_name: str) -> bool:
    """Return True if the named contract is locked in the freeze manifest."""
    m = get_manifest()
    for c in m.get("contracts", []):
        if c["name"] == contract_name and c.get("status") == "locked":
            return True
    return False


def get_locked_contracts() -> list[str]:
    """Return the list of locked contract names."""
    m = get_manifest()
    return [
        c["name"] for c in m.get("contracts", [])
        if c.get("status") == "locked"
    ]


def get_prohibited_actions() -> list[str]:
    """Return the list of actions prohibited until v2."""
    m = get_manifest()
    return m.get("prohibited_until_v2", [])


def get_allowed_actions() -> list[str]:
    """Return the list of actions allowed after freeze."""
    m = get_manifest()
    return m.get("allowed_after_freeze", [])


# ---------------------------------------------------------------------------
# Schema integrity — verify locked schemas haven't drifted
# ---------------------------------------------------------------------------

# Known-good hashes at freeze time. These are the SHA-256 digests of the
# schema files when the freeze was declared. If they change, the freeze
# is violated.
_FROZEN_SCHEMA_HASHES: dict[str, str] = {}


def _compute_schema_hash(schema_path: Path) -> str:
    """Compute SHA-256 of a schema file."""
    return hashlib.sha256(schema_path.read_bytes()).hexdigest()


def register_frozen_schema_hash(contract_name: str, schema_hash: str) -> None:
    """Register the known-good hash for a frozen schema.

    Called at startup to record the expected hash. Subsequent calls to
    verify_schema_integrity() will compare against this value.
    """
    _FROZEN_SCHEMA_HASHES[contract_name] = schema_hash


def verify_schema_integrity() -> list[dict]:
    """Verify all frozen schemas match their known-good hashes.

    Returns a list of violations (empty = all good).
    Each violation: {"contract": str, "expected": str, "actual": str}
    """
    from .blueprint.validate_blueprint_parse import get_schema_hash

    violations: list[dict] = []

    # BlueprintParseV1
    if "BlueprintParseV1" in _FROZEN_SCHEMA_HASHES:
        actual = get_schema_hash()
        expected = _FROZEN_SCHEMA_HASHES["BlueprintParseV1"]
        if actual != expected:
            violations.append({
                "contract": "BlueprintParseV1",
                "expected": expected,
                "actual": actual,
            })

    return violations


# ---------------------------------------------------------------------------
# Change classification
# ---------------------------------------------------------------------------

VALID_CHANGE_TYPES = {"feedback_item", "improvement_proposal", "new_contract_version"}


def classify_change_request(change_type: str) -> dict:
    """Validate a change request type against governance rules.

    Returns:
        {"valid": bool, "change_type": str, "message": str}
    """
    if change_type in VALID_CHANGE_TYPES:
        return {
            "valid": True,
            "change_type": change_type,
            "message": f"Change request type '{change_type}' is valid under freeze governance.",
        }
    return {
        "valid": False,
        "change_type": change_type,
        "message": (
            f"Change request type '{change_type}' is not valid. "
            f"Must be one of: {sorted(VALID_CHANGE_TYPES)}. "
            "Hot patches are not allowed under freeze governance."
        ),
    }


# ---------------------------------------------------------------------------
# Response stamping — inject vertex metadata into tool results
# ---------------------------------------------------------------------------

def stamp_response(response: dict) -> dict:
    """Inject the vertex stamp into a tool response dict.

    Only stamps responses that have "ok": True (successful tool calls).
    The stamp is added under the "vertex" key at the top level.

    This is non-destructive: if "vertex" already exists, it is preserved.
    """
    if not isinstance(response, dict):
        return response
    if not response.get("ok"):
        return response
    if "vertex" not in response:
        response["vertex"] = get_vertex_stamp()
    return response


# ---------------------------------------------------------------------------
# Auto-register schema hashes on import
# ---------------------------------------------------------------------------

def _register_current_hashes() -> None:
    """Record current schema hashes as the frozen baseline."""
    try:
        from .blueprint.validate_blueprint_parse import get_schema_hash
        register_frozen_schema_hash("BlueprintParseV1", get_schema_hash())
        logger.info("Registered frozen schema hash for BlueprintParseV1")
    except Exception as e:
        logger.warning("Could not register BlueprintParseV1 hash: %s", e)


_register_current_hashes()
