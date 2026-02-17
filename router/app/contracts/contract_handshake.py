"""Startup contract handshake with launchbase-platform.

On boot, agent-stack calls the platform's
  admin.blueprintIngestion.getContractInfo
endpoint and compares contract.name, version, and schema_hash against
the local freeze manifest.

If any mismatch is detected:
  - The mismatch is logged as an error.
  - Frozen tool dispatch is blocked (returns 503 CONTRACT_MISMATCH).
  - There is NO fallback and NO silent accept.

This runs ONCE at startup — not per request.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from .governance import get_manifest, get_manifest_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PLATFORM_BASE_URL = os.getenv("PLATFORM_BASE_URL", "")
PLATFORM_AUTH_TOKEN = os.getenv("PLATFORM_AUTH_TOKEN", "")
HANDSHAKE_TIMEOUT = int(os.getenv("CONTRACT_HANDSHAKE_TIMEOUT", "10"))
HANDSHAKE_MAX_RETRIES = int(os.getenv("CONTRACT_HANDSHAKE_RETRIES", "3"))
HANDSHAKE_FAIL_EXIT = os.getenv("CONTRACT_HANDSHAKE_FAIL_EXIT", "false").lower() == "true"

# tRPC endpoint path for contract info
_TRPC_PROCEDURE = "admin.blueprintIngestion.getContractInfo"

# ---------------------------------------------------------------------------
# Handshake state (module-level singleton)
# ---------------------------------------------------------------------------

_handshake_passed: bool | None = None  # None = not yet attempted
_handshake_errors: list[str] = []


def handshake_status() -> dict:
    """Return the current handshake state for diagnostics."""
    return {
        "passed": _handshake_passed,
        "errors": list(_handshake_errors),
    }


def is_handshake_valid() -> bool:
    """Return True only if the handshake ran and passed.

    If the handshake was never attempted (no PLATFORM_BASE_URL configured),
    this returns False — frozen tools will not run without a verified
    platform connection.
    """
    return _handshake_passed is True


# ---------------------------------------------------------------------------
# Platform call
# ---------------------------------------------------------------------------

def _fetch_platform_contracts() -> dict[str, Any]:
    """Call admin.blueprintIngestion.getContractInfo on the platform.

    Returns the JSON response body.
    Raises on network / HTTP errors.
    """
    if not PLATFORM_BASE_URL:
        raise RuntimeError(
            "PLATFORM_BASE_URL is not set — cannot perform contract handshake"
        )

    url = urljoin(
        PLATFORM_BASE_URL.rstrip("/") + "/",
        f"api/trpc/{_TRPC_PROCEDURE}",
    )

    headers: dict[str, str] = {}
    if PLATFORM_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {PLATFORM_AUTH_TOKEN}"

    resp = httpx.get(url, headers=headers, timeout=HANDSHAKE_TIMEOUT)
    resp.raise_for_status()

    body = resp.json()
    # tRPC wraps the result under "result.data"
    if "result" in body and "data" in body["result"]:
        return body["result"]["data"]
    return body


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

def _compare_contracts(platform_data: dict[str, Any]) -> list[str]:
    """Compare platform contract info against local freeze manifest.

    Returns a list of mismatch descriptions (empty = all good).
    """
    errors: list[str] = []
    manifest = get_manifest()
    local_hash = get_manifest_hash()

    # Platform should return a list of contracts
    platform_contracts: list[dict] = platform_data.get("contracts", [])
    if not platform_contracts:
        errors.append(
            "Platform returned no contracts — cannot verify handshake"
        )
        return errors

    # Index local contracts by name
    local_contracts = {c["name"]: c for c in manifest.get("contracts", [])}

    for pc in platform_contracts:
        name = pc.get("name", "<unknown>")
        local = local_contracts.get(name)

        if local is None:
            errors.append(
                f"Platform has contract '{name}' not present in local manifest"
            )
            continue

        # Version check
        if pc.get("version") != local.get("version"):
            errors.append(
                f"Contract '{name}' version mismatch: "
                f"platform={pc.get('version')} local={local.get('version')}"
            )

        # Schema hash check
        if pc.get("schema_hash") and pc["schema_hash"] != local.get("schema_hash"):
            errors.append(
                f"Contract '{name}' schema_hash mismatch: "
                f"platform={pc.get('schema_hash')} local={local.get('schema_hash')}"
            )

    # Also check for local contracts the platform doesn't know about
    platform_names = {pc.get("name") for pc in platform_contracts}
    for local_name in local_contracts:
        if local_name not in platform_names:
            errors.append(
                f"Local contract '{local_name}' not found on platform"
            )

    # Manifest-level hash comparison (if platform provides one)
    platform_manifest_hash = platform_data.get("manifest_hash")
    if platform_manifest_hash and platform_manifest_hash != local_hash:
        errors.append(
            f"Manifest hash mismatch: "
            f"platform={platform_manifest_hash} local={local_hash}"
        )

    return errors


# ---------------------------------------------------------------------------
# Public entry point — call once at startup
# ---------------------------------------------------------------------------

def run_handshake() -> bool:
    """Execute the contract handshake against the platform.

    Retries up to HANDSHAKE_MAX_RETRIES times with exponential backoff
    (2s, 4s, 8s) on network errors. Contract mismatches are NOT retried
    — they indicate a real version divergence.

    Returns True if all contracts match, False otherwise.
    Sets module-level state so is_handshake_valid() can be used later.

    If CONTRACT_HANDSHAKE_FAIL_EXIT=true, exits the process on failure
    (exit code 78 = EX_CONFIG) to prevent a partially-valid runtime.
    """
    global _handshake_passed, _handshake_errors

    if not PLATFORM_BASE_URL:
        _handshake_passed = False
        _handshake_errors = [
            "PLATFORM_BASE_URL not configured — handshake skipped, "
            "frozen tools will be blocked"
        ]
        logger.error("CONTRACT HANDSHAKE FAILED: %s", _handshake_errors[0])
        _maybe_exit()
        return False

    logger.info(
        "Starting contract handshake with platform at %s", PLATFORM_BASE_URL
    )

    # --- Retry loop for network errors ---
    platform_data = None
    last_network_error = None

    for attempt in range(1, HANDSHAKE_MAX_RETRIES + 1):
        try:
            platform_data = _fetch_platform_contracts()
            last_network_error = None
            break
        except Exception as e:
            last_network_error = e
            if attempt < HANDSHAKE_MAX_RETRIES:
                backoff = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(
                    "CONTRACT HANDSHAKE attempt %d/%d failed: %s — retrying in %ds",
                    attempt, HANDSHAKE_MAX_RETRIES, e, backoff,
                )
                time.sleep(backoff)
            else:
                logger.error(
                    "CONTRACT HANDSHAKE attempt %d/%d failed: %s — no retries left",
                    attempt, HANDSHAKE_MAX_RETRIES, e,
                )

    if platform_data is None:
        _handshake_passed = False
        _handshake_errors = [
            f"Platform unreachable after {HANDSHAKE_MAX_RETRIES} attempts: "
            f"{last_network_error}"
        ]
        logger.error("CONTRACT HANDSHAKE FAILED: %s", _handshake_errors[0])
        _maybe_exit()
        return False

    # --- Compare contracts (no retry — mismatch is definitive) ---
    errors = _compare_contracts(platform_data)

    if errors:
        _handshake_passed = False
        _handshake_errors = errors
        for err in errors:
            logger.error("CONTRACT MISMATCH: %s", err)
        logger.error(
            "CONTRACT HANDSHAKE FAILED — frozen tools will refuse to dispatch"
        )
        _maybe_exit()
        return False

    _handshake_passed = True
    _handshake_errors = []
    logger.info("CONTRACT HANDSHAKE PASSED — all contracts verified")
    return True


def _maybe_exit() -> None:
    """Exit the process if hard-fail mode is enabled."""
    if HANDSHAKE_FAIL_EXIT:
        logger.critical(
            "CONTRACT_HANDSHAKE_FAIL_EXIT is set — terminating process (exit 78)"
        )
        sys.exit(78)  # EX_CONFIG
