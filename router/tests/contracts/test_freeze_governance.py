"""Tests for IBEW LV V1 freeze governance.

Validates:
  - Freeze manifest integrity and structure
  - Vertex stamp correctness
  - Schema integrity verification (hash-locked)
  - Governance rules enforcement
  - Tool responses include vertex metadata
  - Prohibited actions are declared
  - Change classification works correctly
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))

from contracts.governance import (
    get_manifest,
    get_manifest_hash,
    get_vertex_stamp,
    is_frozen,
    is_contract_locked,
    get_locked_contracts,
    get_prohibited_actions,
    get_allowed_actions,
    verify_schema_integrity,
    classify_change_request,
    stamp_response,
    VALID_CHANGE_TYPES,
)
from contracts.blueprint.validate_blueprint_parse import get_schema_hash


# ===================================================================
# Freeze Manifest Structure
# ===================================================================

class TestFreezeManifest(unittest.TestCase):
    """Test the freeze manifest file is correct and complete."""

    def test_manifest_loads(self):
        m = get_manifest()
        self.assertIsInstance(m, dict)
        self.assertIn("vertex", m)

    def test_manifest_vertex(self):
        m = get_manifest()
        self.assertEqual(m["vertex"], "IBEW_LV")

    def test_manifest_version(self):
        m = get_manifest()
        self.assertEqual(m["version"], "1.0.0")
        # semver pattern
        self.assertRegex(m["version"], r"^\d+\.\d+\.\d+$")

    def test_manifest_status_frozen(self):
        m = get_manifest()
        self.assertEqual(m["status"], "frozen")

    def test_manifest_frozen_at(self):
        m = get_manifest()
        self.assertEqual(m["frozen_at"], "2026-02-15")
        # ISO date pattern
        self.assertRegex(m["frozen_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_manifest_has_contracts(self):
        m = get_manifest()
        contracts = m.get("contracts", [])
        self.assertGreaterEqual(len(contracts), 2)
        names = [c["name"] for c in contracts]
        self.assertIn("BlueprintParseV1", names)
        self.assertIn("EstimateChainV1", names)

    def test_all_contracts_locked(self):
        m = get_manifest()
        for c in m["contracts"]:
            self.assertEqual(c["status"], "locked", f"{c['name']} should be locked")

    def test_manifest_has_intelligence(self):
        m = get_manifest()
        self.assertIn("intelligence", m)
        self.assertEqual(m["intelligence"]["status"], "locked")

    def test_manifest_has_outputs(self):
        m = get_manifest()
        self.assertIn("outputs", m)
        self.assertEqual(m["outputs"]["status"], "locked")

    def test_manifest_has_prohibited_actions(self):
        m = get_manifest()
        prohibited = m.get("prohibited_until_v2", [])
        self.assertGreaterEqual(len(prohibited), 5)

    def test_manifest_has_allowed_actions(self):
        m = get_manifest()
        allowed = m.get("allowed_after_freeze", [])
        self.assertGreaterEqual(len(allowed), 5)

    def test_manifest_has_governance_rules(self):
        m = get_manifest()
        self.assertIn("governance", m)
        self.assertIn("change_request_types", m["governance"])
        self.assertIn("rule", m["governance"])

    def test_manifest_hash_stable(self):
        """Hash doesn't change between calls (singleton loaded once)."""
        h1 = get_manifest_hash()
        h2 = get_manifest_hash()
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # SHA-256 hex


# ===================================================================
# Vertex Stamp
# ===================================================================

class TestVertexStamp(unittest.TestCase):
    """Test the vertex stamp block for API responses."""

    def test_stamp_structure(self):
        stamp = get_vertex_stamp()
        self.assertEqual(stamp["vertex"], "IBEW_LV")
        self.assertEqual(stamp["version"], "1.0.0")
        self.assertEqual(stamp["status"], "frozen")
        self.assertEqual(stamp["frozen_at"], "2026-02-15")
        self.assertIn("contracts", stamp)
        self.assertIn("manifest_hash", stamp)

    def test_stamp_contracts_list(self):
        stamp = get_vertex_stamp()
        self.assertIn("BlueprintParseV1", stamp["contracts"])
        self.assertIn("EstimateChainV1", stamp["contracts"])

    def test_stamp_manifest_hash_matches(self):
        stamp = get_vertex_stamp()
        self.assertEqual(stamp["manifest_hash"], get_manifest_hash())

    def test_stamp_is_deterministic(self):
        """Same stamp every time (no random values)."""
        s1 = get_vertex_stamp()
        s2 = get_vertex_stamp()
        self.assertEqual(s1, s2)


# ===================================================================
# Freeze Status Queries
# ===================================================================

class TestFreezeStatus(unittest.TestCase):
    """Test freeze status query functions."""

    def test_is_frozen(self):
        self.assertTrue(is_frozen())

    def test_blueprint_parse_locked(self):
        self.assertTrue(is_contract_locked("BlueprintParseV1"))

    def test_estimate_chain_locked(self):
        self.assertTrue(is_contract_locked("EstimateChainV1"))

    def test_unknown_contract_not_locked(self):
        self.assertFalse(is_contract_locked("FakeContractV99"))

    def test_locked_contracts_list(self):
        locked = get_locked_contracts()
        self.assertIn("BlueprintParseV1", locked)
        self.assertIn("EstimateChainV1", locked)

    def test_prohibited_actions_not_empty(self):
        prohibited = get_prohibited_actions()
        self.assertGreater(len(prohibited), 0)
        # Key prohibitions
        has_auto_learning = any("auto-learning" in p.lower() for p in prohibited)
        has_silent_overrides = any("silent override" in p.lower() for p in prohibited)
        self.assertTrue(has_auto_learning)
        self.assertTrue(has_silent_overrides)

    def test_allowed_actions_not_empty(self):
        allowed = get_allowed_actions()
        self.assertGreater(len(allowed), 0)
        has_bug_fixes = any("bug fix" in a.lower() for a in allowed)
        self.assertTrue(has_bug_fixes)


# ===================================================================
# Schema Integrity
# ===================================================================

class TestSchemaIntegrity(unittest.TestCase):
    """Test that frozen schemas haven't drifted."""

    def test_no_violations(self):
        """Current schemas match frozen hashes."""
        violations = verify_schema_integrity()
        self.assertEqual(violations, [])

    def test_blueprint_parse_schema_hash_registered(self):
        """The BlueprintParseV1 schema hash is registered at import time."""
        from contracts.governance import _FROZEN_SCHEMA_HASHES
        self.assertIn("BlueprintParseV1", _FROZEN_SCHEMA_HASHES)

    def test_schema_file_untampered(self):
        """Verify the schema file on disk matches the registered hash."""
        schema_path = Path(__file__).parent.parent.parent / "app" / "contracts" / "blueprint" / "BlueprintParseV1.schema.json"
        actual_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()
        registered_hash = get_schema_hash()
        self.assertEqual(actual_hash, registered_hash)


# ===================================================================
# Change Classification / Governance Gate
# ===================================================================

class TestChangeClassification(unittest.TestCase):
    """Test governance rule enforcement."""

    def test_valid_feedback_item(self):
        result = classify_change_request("feedback_item")
        self.assertTrue(result["valid"])

    def test_valid_improvement_proposal(self):
        result = classify_change_request("improvement_proposal")
        self.assertTrue(result["valid"])

    def test_valid_new_contract_version(self):
        result = classify_change_request("new_contract_version")
        self.assertTrue(result["valid"])

    def test_hot_patch_rejected(self):
        result = classify_change_request("hot_patch")
        self.assertFalse(result["valid"])
        self.assertIn("not valid", result["message"])

    def test_emergency_fix_rejected(self):
        result = classify_change_request("emergency_fix")
        self.assertFalse(result["valid"])

    def test_arbitrary_string_rejected(self):
        result = classify_change_request("just_do_it")
        self.assertFalse(result["valid"])

    def test_valid_types_match_manifest(self):
        """VALID_CHANGE_TYPES in code matches governance.change_request_types in manifest."""
        m = get_manifest()
        manifest_types = set(m["governance"]["change_request_types"])
        self.assertEqual(VALID_CHANGE_TYPES, manifest_types)


# ===================================================================
# Response Stamping
# ===================================================================

class TestResponseStamping(unittest.TestCase):
    """Test that stamp_response correctly injects vertex metadata."""

    def test_stamps_ok_response(self):
        resp = {"ok": True, "data": "hello"}
        stamped = stamp_response(resp)
        self.assertIn("vertex", stamped)
        self.assertEqual(stamped["vertex"]["vertex"], "IBEW_LV")

    def test_does_not_stamp_error_response(self):
        resp = {"ok": False, "error": "something failed"}
        stamped = stamp_response(resp)
        self.assertNotIn("vertex", stamped)

    def test_does_not_overwrite_existing_vertex(self):
        custom_vertex = {"vertex": "CUSTOM", "version": "9.9.9"}
        resp = {"ok": True, "vertex": custom_vertex}
        stamped = stamp_response(resp)
        self.assertEqual(stamped["vertex"]["vertex"], "CUSTOM")

    def test_handles_non_dict(self):
        result = stamp_response("not a dict")
        self.assertEqual(result, "not a dict")

    def test_handles_none_ok(self):
        resp = {"data": "no ok field"}
        stamped = stamp_response(resp)
        self.assertNotIn("vertex", stamped)


# ===================================================================
# Tool Integration — vertex stamp in tool responses
# ===================================================================

class TestToolVertexIntegration(unittest.TestCase):
    """Test that stamp_response correctly stamps tool-like responses."""

    def test_stamps_list_models_style_response(self):
        """stamp_response stamps ok:True tool responses with vertex."""
        from model_registry import list_models, get_active_model
        # Simulate what blueprint_list_models returns
        models = list_models()
        active = get_active_model()
        resp = {
            "ok": True,
            "model_count": len(models),
            "active_model_id": active.model_id,
            "models": models,
        }
        stamped = stamp_response(resp)
        self.assertIn("vertex", stamped)
        self.assertEqual(stamped["vertex"]["vertex"], "IBEW_LV")
        self.assertEqual(stamped["vertex"]["status"], "frozen")

    def test_error_response_not_stamped(self):
        """stamp_response does NOT stamp ok:False responses."""
        resp = {"ok": False, "error": "Unknown tool: fake"}
        stamped = stamp_response(resp)
        self.assertNotIn("vertex", stamped)

    def test_vendor_style_response_has_vertex(self):
        """stamp_response stamps vendor-style ok:True responses."""
        from vendor_pricing_tools import vendor_list_sources
        resp = vendor_list_sources()
        stamped = stamp_response(resp)
        self.assertTrue(stamped.get("ok"))
        self.assertIn("vertex", stamped)
        self.assertEqual(stamped["vertex"]["version"], "1.0.0")


# ===================================================================
# Freeze File Integrity
# ===================================================================

class TestFreezeFileIntegrity(unittest.TestCase):
    """Test the freeze manifest file itself."""

    def test_json_valid(self):
        freeze_path = Path(__file__).parent.parent.parent / "app" / "contracts" / "IBEW_LV_V1.freeze.json"
        raw = freeze_path.read_text()
        data = json.loads(raw)
        self.assertIsInstance(data, dict)

    def test_file_hash_matches_module(self):
        freeze_path = Path(__file__).parent.parent.parent / "app" / "contracts" / "IBEW_LV_V1.freeze.json"
        file_hash = hashlib.sha256(freeze_path.read_bytes()).hexdigest()
        module_hash = get_manifest_hash()
        self.assertEqual(file_hash, module_hash)

    def test_gap_detection_rules_declared(self):
        m = get_manifest()
        intel = m["intelligence"]
        gap_component = None
        for comp in intel["components"]:
            if comp["name"] == "gap_detection_rules":
                gap_component = comp
                break
        self.assertIsNotNone(gap_component)
        self.assertEqual(gap_component["rules_locked"], ["G1", "G2", "G3", "G4", "G5", "G6"])

    def test_model_registry_frozen(self):
        m = get_manifest()
        intel = m["intelligence"]
        model_component = None
        for comp in intel["components"]:
            if comp["name"] == "symbol_detection_model_registry":
                model_component = comp
                break
        self.assertIsNotNone(model_component)
        self.assertIn("yolov8n-blueprint-v1", model_component["locked_models"])
        self.assertIn("yolov8s-blueprint-v2", model_component["locked_models"])
        self.assertIn("yolov8m-blueprint-v3", model_component["locked_models"])

    def test_output_formats_frozen(self):
        m = get_manifest()
        outputs = m["outputs"]
        format_names = [f["name"] for f in outputs["formats"]]
        self.assertIn("excel_export", format_names)
        self.assertIn("bluebeam_csv", format_names)


# ===================================================================
# Cross-Module Consistency
# ===================================================================

class TestCrossModuleConsistency(unittest.TestCase):
    """Test that frozen values are consistent across modules."""

    def test_contract_name_matches_schema(self):
        """BlueprintParseV1 contract name in governance matches schema $id."""
        schema_path = Path(__file__).parent.parent.parent / "app" / "contracts" / "blueprint" / "BlueprintParseV1.schema.json"
        schema = json.loads(schema_path.read_text())
        locked = get_locked_contracts()
        self.assertIn(schema["$id"], locked)

    def test_model_registry_models_match_frozen_list(self):
        """Model registry models match the frozen manifest's locked_models."""
        from model_registry import list_models
        m = get_manifest()
        model_component = next(
            c for c in m["intelligence"]["components"]
            if c["name"] == "symbol_detection_model_registry"
        )
        frozen_ids = set(model_component["locked_models"])
        registry_ids = {m["model_id"] for m in list_models()}
        # All frozen models must exist in registry
        self.assertTrue(frozen_ids.issubset(registry_ids),
                        f"Frozen models missing from registry: {frozen_ids - registry_ids}")

    def test_schema_hash_chain_unbroken(self):
        """Schema hash from validate module matches hash from governance."""
        from contracts.governance import _FROZEN_SCHEMA_HASHES
        validate_hash = get_schema_hash()
        frozen_hash = _FROZEN_SCHEMA_HASHES.get("BlueprintParseV1")
        self.assertEqual(validate_hash, frozen_hash)


if __name__ == "__main__":
    unittest.main()
