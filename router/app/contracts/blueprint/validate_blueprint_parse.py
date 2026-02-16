"""Runtime validator for BlueprintParseV1 contract.

Validates parse output against the frozen JSON Schema before returning
it to callers. Returns structured validation errors — never raw crashes.

Also computes the schema_hash (SHA-256 of the schema file) for inclusion
in the contract block.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator, ValidationError

# ---------------------------------------------------------------------------
# Schema loading (singleton)
# ---------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).parent
_SCHEMA_PATH = _SCHEMA_DIR / "BlueprintParseV1.schema.json"

_schema: dict | None = None
_schema_hash: str | None = None
_validator: Draft202012Validator | None = None


def _load_schema() -> tuple[dict, str, Draft202012Validator]:
    """Load schema from disk, compute hash, build validator. Cached."""
    global _schema, _schema_hash, _validator
    if _schema is not None:
        return _schema, _schema_hash, _validator  # type: ignore[return-value]

    raw = _SCHEMA_PATH.read_bytes()
    _schema_hash = hashlib.sha256(raw).hexdigest()
    _schema = json.loads(raw)
    _validator = Draft202012Validator(_schema)
    return _schema, _schema_hash, _validator


def get_schema_hash() -> str:
    """Return the SHA-256 hex digest of BlueprintParseV1.schema.json."""
    _, h, _ = _load_schema()
    return h


def get_schema() -> dict:
    """Return the parsed JSON Schema dict."""
    s, _, _ = _load_schema()
    return s


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------

CONTRACT_NAME = "BlueprintParseV1"
CONTRACT_VERSION = "1.0.0"

# Tool version — pinned per release; updated when the tool changes behavior
TOOL_VERSION = "1.1.0"
RUNTIME = "agent-stack"


def build_contract_block(model_version: str | None = None) -> dict:
    """Build the contract metadata block for inclusion in tool output."""
    return {
        "name": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "schema_hash": get_schema_hash(),
        "producer": {
            "tool": "blueprint_parse_document",
            "tool_version": TOOL_VERSION,
            "runtime": RUNTIME,
            "model_version": model_version,
        },
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_parse_output(data: dict) -> list[dict]:
    """Validate a parse output dict against BlueprintParseV1.

    Returns a list of error dicts (empty = valid).
    Each error: {"code": str, "message": str, "path": str}
    """
    _, _, validator = _load_schema()
    errors: list[dict] = []

    for err in validator.iter_errors(data):
        path_str = ".".join(str(p) for p in err.absolute_path) or "$"
        errors.append({
            "code": "SCHEMA_VALIDATION_ERROR",
            "message": err.message[:300],
            "path": path_str,
        })

    return errors


def validate_or_error(data: dict) -> dict | None:
    """Validate and return structured error response if invalid.

    Returns None if valid, or an error dict if invalid.
    """
    errors = validate_parse_output(data)
    if not errors:
        return None

    return {
        "ok": False,
        "error": "BlueprintParseV1 schema validation failed",
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "validation_errors": errors[:20],  # cap at 20
    }
