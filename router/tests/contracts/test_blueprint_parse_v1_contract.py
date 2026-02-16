"""Acceptance tests for PR-A1.5: BlueprintParseV1 protocol hardening.

Validates:
  - Schema file loads and has stable hash
  - Known-good parse output validates successfully
  - Deliberately malformed output fails validation with structured error
  - Contract metadata present in tool response (name, version, schema_hash, producer)
  - schema_hash matches the frozen schema file
  - detect_symbols includes producer metadata with schema_hash and model_version
  - Validation returns structured errors, never raw crashes
  - No 4xx/422 drift on contract-hardened tools
"""

import copy
import hashlib
import importlib
import json
import os

import fitz
import pytest
from fastapi.testclient import TestClient


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    ws_dir = ws_root / "contract-proj"
    ws_dir.mkdir()

    monkeypatch.setenv("WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("ROUTER_AUTH_TOKEN", "")

    from router.app import workspace_tools, blueprint_parse_tools, blueprint_detect_tools, tools, main
    from router.app.contracts.blueprint import validate_blueprint_parse
    importlib.reload(validate_blueprint_parse)
    importlib.reload(blueprint_parse_tools)
    importlib.reload(blueprint_detect_tools)
    importlib.reload(workspace_tools)
    importlib.reload(tools)
    importlib.reload(main)

    yield str(ws_root)


@pytest.fixture()
def ws_dir(_workspace_root):
    return os.path.join(_workspace_root, "contract-proj")


@pytest.fixture()
def client():
    from router.app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def sample_pdf(ws_dir):
    """Create a minimal blueprint PDF."""
    filename = "plans/test.pdf"
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((72, 72), "TEST ELECTRICAL PLAN E-001", fontsize=14)
    page.insert_text(
        (72, 140),
        "4x Cat6A Data Drop\n2x Wireless AP\n1x CCTV Camera\n",
        fontsize=10,
    )
    page.insert_text(
        (560, 80),
        "LEGEND\nDD - Data Drop\nAP - Access Point\n",
        fontsize=8,
    )
    page.insert_text(
        (500, 520),
        "Drawing No: E-001\nSheet No: 1\nScale: NTS\nDate: 2024-01-01",
        fontsize=7,
    )
    doc.save(full)
    doc.close()
    return filename


def _call_parse(client, pdf_path, **extra):
    args = {"workspace": "contract-proj", "pdf_path": pdf_path}
    args.update(extra)
    return client.post("/tool", json={
        "tool_call": {"name": "blueprint_parse_document", "arguments": args}
    })


def _call_detect(client, pdf_path, **extra):
    args = {"workspace": "contract-proj", "pdf_path": pdf_path}
    args.update(extra)
    return client.post("/tool", json={
        "tool_call": {"name": "blueprint_detect_symbols", "arguments": args}
    })


# =====================================================================
# Test: Schema file integrity
# =====================================================================

class TestSchemaFile:
    def test_schema_loads(self):
        from router.app.contracts.blueprint.validate_blueprint_parse import get_schema
        schema = get_schema()
        assert schema["$id"] == "BlueprintParseV1"
        assert schema["title"] == "BlueprintParseV1"
        assert "contract" in schema["required"]
        assert "errors" in schema["required"]

    def test_schema_hash_is_stable(self):
        """Schema hash is deterministic SHA-256 of the file bytes."""
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            get_schema_hash,
            _SCHEMA_PATH,
        )
        raw = _SCHEMA_PATH.read_bytes()
        expected = hashlib.sha256(raw).hexdigest()
        assert get_schema_hash() == expected
        assert len(get_schema_hash()) == 64  # SHA-256 hex = 64 chars

    def test_schema_has_bbox_definition(self):
        from router.app.contracts.blueprint.validate_blueprint_parse import get_schema
        schema = get_schema()
        bbox_def = schema["$defs"]["bbox"]
        assert bbox_def["type"] == "array"
        assert bbox_def["minItems"] == 4
        assert bbox_def["maxItems"] == 4

    def test_schema_has_contract_definition(self):
        from router.app.contracts.blueprint.validate_blueprint_parse import get_schema
        schema = get_schema()
        contract_def = schema["$defs"]["contract"]
        assert "name" in contract_def["required"]
        assert "version" in contract_def["required"]
        assert "schema_hash" in contract_def["required"]
        assert "producer" in contract_def["required"]


# =====================================================================
# Test: Known-good output validates (green test)
# =====================================================================

class TestKnownGoodValidation:
    def test_real_parse_output_validates(self, client, sample_pdf):
        """A real parse output passes schema validation."""
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert body["ok"] is True

        # Validate independently — strip 'vertex' governance metadata
        # which is injected post-validation and not part of the schema.
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        body.pop("vertex", None)
        errors = validate_parse_output(body)
        assert errors == [], f"Validation errors: {errors}"

    def test_contract_block_present(self, client, sample_pdf):
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert "contract" in body
        contract = body["contract"]
        assert contract["name"] == "BlueprintParseV1"
        assert contract["version"] == "1.0.0"
        assert len(contract["schema_hash"]) == 64
        assert contract["producer"]["tool"] == "blueprint_parse_document"
        assert contract["producer"]["runtime"] == "agent-stack"
        assert "tool_version" in contract["producer"]

    def test_errors_array_present_and_empty(self, client, sample_pdf):
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert "errors" in body
        assert isinstance(body["errors"], list)
        assert len(body["errors"]) == 0

    def test_schema_hash_matches_frozen_file(self, client, sample_pdf):
        """schema_hash in response matches the actual schema file hash."""
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            get_schema_hash,
        )
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert body["contract"]["schema_hash"] == get_schema_hash()


# =====================================================================
# Test: Malformed output fails validation with structured error
# =====================================================================

class TestMalformedValidation:
    def _good_output(self, client, sample_pdf):
        """Get a known-good output to mutate.

        Strips the 'vertex' key which is governance metadata injected
        post-validation and is not part of the BlueprintParseV1 schema.
        """
        resp = _call_parse(client, sample_pdf)
        data = resp.json()
        data.pop("vertex", None)
        return data

    def test_missing_contract_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        del data["contract"]
        errors = validate_parse_output(data)
        assert len(errors) > 0
        assert any("contract" in e["message"].lower() or "contract" in e["path"]
                    for e in errors)

    def test_missing_pages_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        del data["pages"]
        errors = validate_parse_output(data)
        assert len(errors) > 0

    def test_null_pages_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        data["pages"] = None
        errors = validate_parse_output(data)
        assert len(errors) > 0

    def test_wrong_schema_version_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        data["schema_version"] = "BlueprintParseV99"
        errors = validate_parse_output(data)
        assert len(errors) > 0

    def test_invalid_bbox_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        # Corrupt a bbox to have 3 elements instead of 4
        if data["pages"] and data["pages"][0]["text_blocks"]:
            data["pages"][0]["text_blocks"][0]["bbox"] = [1, 2, 3]
            errors = validate_parse_output(data)
            assert len(errors) > 0

    def test_extra_top_level_field_fails(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_parse_output,
        )
        data = self._good_output(client, sample_pdf)
        data["rogue_field"] = "should not be here"
        errors = validate_parse_output(data)
        assert len(errors) > 0

    def test_validate_or_error_returns_structured(self, client, sample_pdf):
        """validate_or_error returns a structured error dict, not a raw crash."""
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_or_error,
        )
        data = self._good_output(client, sample_pdf)
        del data["contract"]
        result = validate_or_error(data)
        assert result is not None
        assert result["ok"] is False
        assert "validation_errors" in result
        assert isinstance(result["validation_errors"], list)
        assert result["contract"] == "BlueprintParseV1"

    def test_validate_or_error_none_when_valid(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            validate_or_error,
        )
        data = self._good_output(client, sample_pdf)
        result = validate_or_error(data)
        assert result is None


# =====================================================================
# Test: detect_symbols includes producer metadata
# =====================================================================

class TestDetectProducerMetadata:
    def test_producer_present(self, client, sample_pdf):
        resp = _call_detect(client, sample_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert "producer" in body
        producer = body["producer"]
        assert producer["tool"] == "blueprint_detect_symbols"
        assert "tool_version" in producer
        assert producer["runtime"] == "agent-stack"

    def test_producer_has_model_version(self, client, sample_pdf):
        resp = _call_detect(client, sample_pdf)
        body = resp.json()
        assert body["producer"]["model_version"] is not None
        assert len(body["producer"]["model_version"]) > 0
        # model_version should match what's in the model block
        assert body["producer"]["model_version"] == body["model"]["version"]

    def test_producer_has_schema_hash(self, client, sample_pdf):
        from router.app.contracts.blueprint.validate_blueprint_parse import (
            get_schema_hash,
        )
        resp = _call_detect(client, sample_pdf)
        body = resp.json()
        assert body["producer"]["schema_hash"] == get_schema_hash()
        assert len(body["producer"]["schema_hash"]) == 64

    def test_detect_output_never_null_arrays(self, client, sample_pdf):
        resp = _call_detect(client, sample_pdf)
        body = resp.json()
        assert isinstance(body["detections"], list)
        assert isinstance(body["page_summaries"], list)
        assert isinstance(body["artifacts"], list)


# =====================================================================
# Test: Contract + parse integration (end to end)
# =====================================================================

class TestContractIntegration:
    def test_parse_then_detect_contract_chain(self, client, sample_pdf):
        """Parse output includes contract; detect output includes producer.
        Both reference the same schema_hash."""
        parse_resp = _call_parse(client, sample_pdf)
        parse_body = parse_resp.json()
        detect_resp = _call_detect(client, sample_pdf)
        detect_body = detect_resp.json()

        # Same schema hash across both tools
        parse_hash = parse_body["contract"]["schema_hash"]
        detect_hash = detect_body["producer"]["schema_hash"]
        assert parse_hash == detect_hash

    def test_contract_version_is_semver(self, client, sample_pdf):
        import re
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert re.match(r"\d+\.\d+\.\d+", body["contract"]["version"])

    def test_producer_tool_version_is_semver(self, client, sample_pdf):
        import re
        resp = _call_parse(client, sample_pdf)
        body = resp.json()
        assert re.match(r"\d+\.\d+\.\d+", body["contract"]["producer"]["tool_version"])


# =====================================================================
# Test: No 4xx/422 drift
# =====================================================================

class TestNo422Drift:
    def test_parse_workspace_validation_still_works(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "blueprint_parse_document",
                "arguments": {"workspace": "nonexistent", "pdf_path": "x.pdf"},
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "blueprint_parse_document"

    def test_detect_workspace_validation_still_works(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "blueprint_detect_symbols",
                "arguments": {"workspace": "nonexistent", "pdf_path": "x.pdf"},
            }
        })
        assert resp.status_code == 422

    def test_valid_workspace_with_contract_returns_200(self, client, sample_pdf):
        resp = _call_parse(client, sample_pdf)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
