"""Acceptance tests for PR-A2: blueprint_detect_symbols + model registry.

Validates:
  - SymbolDetectionV1 schema stability
  - Detections have bbox + confidence + class + model_version
  - Overlay images produced on disk
  - Model registry: versioned models, thresholds, active model
  - Run against 3 sample PDFs with stable outputs + artifacts
  - No 4xx/422 drift (workspace validation)
  - Never returns null content
  - All tools registered in /tools endpoint
"""

import importlib
import json
import os

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient


# ---- Sample PDF builders (same 3 as parse tests) ----

def _create_electrical_pdf(ws_dir: str, filename: str = "plans/electrical.pdf") -> str:
    """Sample 1: Electrical plan with LV devices."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()
    page1 = doc.new_page(width=792, height=612)
    page1.insert_text((72, 72), "ELECTRICAL PLAN E-101", fontsize=14)
    page1.insert_text(
        (72, 150),
        "24x Cat6A Data Drop\n"
        "6x Wireless AP (ceiling mount)\n"
        "4x CCTV IP Camera\n"
        "2x Card Reader\n",
        fontsize=10,
    )
    page1.insert_text(
        (72, 280),
        "FIRE ALARM DEVICES\n"
        "8x Smoke Detector\n"
        "4x Horn/Strobe\n"
        "2x Pull Station\n"
        "3x Speaker\n",
        fontsize=10,
    )

    page2 = doc.new_page(width=792, height=612)
    page2.insert_text((72, 72), "ELECTRICAL PLAN E-102", fontsize=14)
    page2.insert_text(
        (72, 140),
        "12x Duplex Outlet\n"
        "4x GFCI Outlet\n"
        "6x Light Fixture\n"
        "3x Switch\n",
        fontsize=10,
    )

    doc.save(full)
    doc.close()
    return filename


def _create_security_pdf(ws_dir: str, filename: str = "plans/security.pdf") -> str:
    """Sample 2: Security system plan."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()
    page1 = doc.new_page(width=792, height=612)
    page1.insert_text((72, 72), "SECURITY SYSTEM PLAN S-001", fontsize=14)
    page1.insert_text(
        (72, 150),
        "MAIN ENTRANCE\n"
        "2x Card Reader (HID iClass)\n"
        "2x Door Contact\n"
        "1x Intercom Station\n"
        "3x CCTV IP Camera\n",
        fontsize=10,
    )
    page1.insert_text(
        (72, 280),
        "LOADING DOCK\n"
        "1x Card Reader\n"
        "1x Door Contact\n"
        "2x CCTV IP Camera\n",
        fontsize=10,
    )

    doc.save(full)
    doc.close()
    return filename


def _create_lowvoltage_pdf(ws_dir: str, filename: str = "plans/lowvoltage.pdf") -> str:
    """Sample 3: Multi-page low-voltage plan."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()

    p1 = doc.new_page(width=792, height=612)
    p1.insert_text((72, 72), "LOW VOLTAGE PLAN LV-001", fontsize=14)
    p1.insert_text(
        (72, 140),
        "IDF ROOM\n"
        "1x Panel\n"
        "48x Cat6A Data Drop\n"
        "8x Wireless AP\n",
        fontsize=10,
    )

    p2 = doc.new_page(width=792, height=612)
    p2.insert_text((72, 72), "LOW VOLTAGE PLAN LV-002", fontsize=14)
    p2.insert_text(
        (72, 140),
        "CONFERENCE ROOMS\n"
        "6x Cat6A Data Drop\n"
        "2x Wireless AP\n"
        "1x Speaker\n",
        fontsize=10,
    )

    p3 = doc.new_page(width=792, height=612)
    p3.insert_text((72, 72), "LOW VOLTAGE PLAN LV-003", fontsize=14)
    p3.insert_text(
        (72, 140),
        "CORRIDOR\n"
        "4x Smoke Detector\n"
        "2x Horn/Strobe\n"
        "1x Pull Station\n"
        "2x CCTV Camera\n"
        "100 LF Conduit\n"
        "50 LF Cable Tray\n",
        fontsize=10,
    )

    doc.save(full)
    doc.close()
    return filename


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    ws_dir = ws_root / "detect-proj"
    ws_dir.mkdir()

    monkeypatch.setenv("WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("ROUTER_AUTH_TOKEN", "")

    from router.app import (
        workspace_tools, blueprint_detect_tools, model_registry, tools, main
    )
    importlib.reload(model_registry)
    importlib.reload(blueprint_detect_tools)
    importlib.reload(workspace_tools)
    importlib.reload(tools)
    importlib.reload(main)

    yield str(ws_root)


@pytest.fixture()
def ws_dir(_workspace_root):
    return os.path.join(_workspace_root, "detect-proj")


@pytest.fixture()
def client():
    from router.app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def electrical_pdf(ws_dir):
    return _create_electrical_pdf(ws_dir)


@pytest.fixture()
def security_pdf(ws_dir):
    return _create_security_pdf(ws_dir)


@pytest.fixture()
def lowvoltage_pdf(ws_dir):
    return _create_lowvoltage_pdf(ws_dir)


def _call_detect(client, pdf_path, **extra):
    args = {"workspace": "detect-proj", "pdf_path": pdf_path}
    args.update(extra)
    return client.post("/tool", json={
        "tool_call": {"name": "blueprint_detect_symbols", "arguments": args}
    })


# =====================================================================
# Test: SymbolDetectionV1 schema stability
# =====================================================================

class TestDetectionSchema:
    REQUIRED_TOP_KEYS = {
        "ok", "schema_version", "detection_id", "generated_at",
        "pdf_path", "page_count", "model", "summary",
        "detections", "page_summaries", "artifacts",
    }
    REQUIRED_MODEL_KEYS = {
        "model_id", "version", "architecture", "input_size",
        "threshold_used", "using_heuristic_fallback",
    }
    REQUIRED_DETECTION_KEYS = {
        "class", "confidence", "bbox", "page", "model_version",
        "detection_method",
    }

    def test_electrical_schema(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        assert resp.status_code == 200
        body = resp.json()

        assert body["ok"] is True
        assert body["schema_version"] == "SymbolDetectionV1"
        assert self.REQUIRED_TOP_KEYS.issubset(set(body.keys()))

        # Model info
        assert self.REQUIRED_MODEL_KEYS.issubset(set(body["model"].keys()))

        # Summary
        assert "total_detections" in body["summary"]
        assert "unique_classes" in body["summary"]
        assert "class_counts" in body["summary"]
        assert body["summary"]["total_detections"] > 0

        # Detections
        assert isinstance(body["detections"], list)
        assert len(body["detections"]) > 0
        for det in body["detections"]:
            assert self.REQUIRED_DETECTION_KEYS.issubset(set(det.keys()))

    def test_security_schema(self, client, security_pdf):
        resp = _call_detect(client, security_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["schema_version"] == "SymbolDetectionV1"
        assert self.REQUIRED_TOP_KEYS.issubset(set(body.keys()))

    def test_multipage_schema(self, client, lowvoltage_pdf):
        resp = _call_detect(client, lowvoltage_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 3
        assert len(body["page_summaries"]) == 3


# =====================================================================
# Test: Detection fields (bbox + confidence + class + model_version)
# =====================================================================

class TestDetectionFields:
    def test_bbox_format(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        for det in body["detections"]:
            bbox = det["bbox"]
            assert isinstance(bbox, list)
            assert len(bbox) == 4
            assert all(isinstance(v, (int, float)) for v in bbox)

    def test_confidence_range(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        for det in body["detections"]:
            assert 0.0 <= det["confidence"] <= 1.0

    def test_class_is_string(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        for det in body["detections"]:
            assert isinstance(det["class"], str)
            assert len(det["class"]) > 0

    def test_model_version_present(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        for det in body["detections"]:
            assert isinstance(det["model_version"], str)
            assert len(det["model_version"]) > 0

    def test_detects_expected_classes(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        detected_classes = {det["class"] for det in body["detections"]}
        # Electrical PDF has data drops, APs, cameras, card readers, etc.
        assert "data_drop" in detected_classes
        assert "wireless_ap" in detected_classes
        assert "cctv_camera" in detected_classes

    def test_security_classes(self, client, security_pdf):
        resp = _call_detect(client, security_pdf)
        body = resp.json()
        detected_classes = {det["class"] for det in body["detections"]}
        assert "card_reader" in detected_classes
        assert "cctv_camera" in detected_classes
        assert "door_contact" in detected_classes


# =====================================================================
# Test: Overlay images
# =====================================================================

class TestOverlayImages:
    def test_overlays_produced(self, client, electrical_pdf, ws_dir):
        resp = _call_detect(client, electrical_pdf, include_overlays=True)
        body = resp.json()
        overlays = [a for a in body["artifacts"] if a["type"] == "detection_overlay"]
        # Should have overlays for pages with detections
        assert len(overlays) >= 1
        for overlay in overlays:
            full_path = os.path.join(ws_dir, overlay["path"])
            assert os.path.isfile(full_path)
            assert overlay["size_bytes"] > 0

    def test_no_overlays_when_disabled(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf, include_overlays=False)
        body = resp.json()
        overlays = [a for a in body["artifacts"] if a["type"] == "detection_overlay"]
        assert len(overlays) == 0

    def test_detection_json_artifact(self, client, electrical_pdf, ws_dir):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        json_artifacts = [a for a in body["artifacts"] if a["type"] == "detection_json"]
        assert len(json_artifacts) == 1
        full_path = os.path.join(ws_dir, json_artifacts[0]["path"])
        assert os.path.isfile(full_path)
        with open(full_path) as f:
            parsed = json.load(f)
        assert parsed["schema_version"] == "SymbolDetectionV1"


# =====================================================================
# Test: Model registry
# =====================================================================

class TestModelRegistry:
    def test_list_models_returns_three(self, client):
        resp = client.post("/tool", json={
            "tool_call": {"name": "blueprint_list_models", "arguments": {}}
        })
        body = resp.json()
        assert body["ok"] is True
        assert body["model_count"] == 3
        assert len(body["models"]) == 3
        model_ids = {m["model_id"] for m in body["models"]}
        assert "yolov8n-blueprint-v1" in model_ids
        assert "yolov8s-blueprint-v2" in model_ids
        assert "yolov8m-blueprint-v3" in model_ids

    def test_model_has_required_fields(self, client):
        resp = client.post("/tool", json={
            "tool_call": {"name": "blueprint_list_models", "arguments": {}}
        })
        body = resp.json()
        for model in body["models"]:
            assert "model_id" in model
            assert "version" in model
            assert "architecture" in model
            assert "input_size" in model
            assert "classes" in model
            assert "class_count" in model
            assert "default_threshold" in model
            assert "class_thresholds" in model
            assert "trainable" in model
            assert isinstance(model["classes"], list)
            assert model["class_count"] == len(model["classes"])

    def test_active_model_defaults_to_v1(self, client):
        resp = client.post("/tool", json={
            "tool_call": {"name": "blueprint_list_models", "arguments": {}}
        })
        body = resp.json()
        assert body["active_model_id"] == "yolov8n-blueprint-v1"

    def test_model_versions_are_semver(self, client):
        resp = client.post("/tool", json={
            "tool_call": {"name": "blueprint_list_models", "arguments": {}}
        })
        body = resp.json()
        import re
        for model in body["models"]:
            assert re.match(r"\d+\.\d+\.\d+", model["version"])

    def test_per_class_thresholds(self):
        from router.app.model_registry import get_model
        model = get_model("yolov8n-blueprint-v1")
        assert model is not None
        # conduit has a custom threshold
        assert model.threshold_for("conduit") == 0.35
        # data_drop uses default
        assert model.threshold_for("data_drop") == 0.25

    def test_select_specific_model(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf, model_id="yolov8s-blueprint-v2")
        body = resp.json()
        assert body["ok"] is True
        assert body["model"]["model_id"] == "yolov8s-blueprint-v2"
        assert body["model"]["version"] == "2.0.0"

    def test_invalid_model_returns_error(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf, model_id="nonexistent-model")
        body = resp.json()
        assert body["ok"] is False
        assert "nonexistent-model" in body["error"]

    def test_threshold_override(self, client, electrical_pdf):
        # With high threshold, fewer detections
        resp_high = _call_detect(client, electrical_pdf, threshold=0.90)
        body_high = resp_high.json()
        # With low threshold, more detections
        resp_low = _call_detect(client, electrical_pdf, threshold=0.10)
        body_low = resp_low.json()
        assert body_low["summary"]["total_detections"] >= body_high["summary"]["total_detections"]


# =====================================================================
# Test: 3 sample PDFs — stable outputs + artifacts
# =====================================================================

class TestThreeSamplePDFs:
    def test_electrical_stable_output(self, client, electrical_pdf, ws_dir):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 2
        assert body["summary"]["total_detections"] > 0
        assert body["summary"]["unique_classes"] > 0
        # Artifacts on disk
        for a in body["artifacts"]:
            if a["type"] != "detection_json":
                full = os.path.join(ws_dir, a["path"])
                assert os.path.isfile(full), f"Artifact missing: {full}"

    def test_security_stable_output(self, client, security_pdf, ws_dir):
        resp = _call_detect(client, security_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 1
        assert body["summary"]["total_detections"] > 0
        detected_classes = set(body["summary"]["class_counts"].keys())
        assert "card_reader" in detected_classes

    def test_lowvoltage_stable_output(self, client, lowvoltage_pdf, ws_dir):
        resp = _call_detect(client, lowvoltage_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 3
        assert body["summary"]["total_detections"] > 0
        # Check page summaries
        for ps in body["page_summaries"]:
            assert "page" in ps
            assert "detection_count" in ps
            assert "class_counts" in ps
            assert "classes_found" in ps
            assert isinstance(ps["classes_found"], list)

    def test_all_three_produce_consistent_schema(
        self, client, electrical_pdf, security_pdf, lowvoltage_pdf
    ):
        """All 3 PDFs produce the same top-level schema keys."""
        expected_keys = {
            "ok", "schema_version", "detection_id", "generated_at",
            "pdf_path", "page_count", "model", "summary",
            "detections", "page_summaries", "artifacts",
        }
        for pdf in [electrical_pdf, security_pdf, lowvoltage_pdf]:
            resp = _call_detect(client, pdf)
            body = resp.json()
            assert expected_keys.issubset(set(body.keys()))
            assert body["schema_version"] == "SymbolDetectionV1"


# =====================================================================
# Test: No 4xx/422 drift
# =====================================================================

class TestWorkspaceValidation:
    def test_invalid_workspace_422(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "blueprint_detect_symbols",
                "arguments": {"workspace": "nonexistent", "pdf_path": "x.pdf"},
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "blueprint_detect_symbols"
        assert "availableWorkspaces" in detail

    def test_valid_workspace_missing_pdf_returns_json_error(self, client):
        resp = _call_detect(client, "nonexistent.pdf")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "not found" in body["error"].lower()


# =====================================================================
# Test: Never null content
# =====================================================================

class TestNeverNullContent:
    def test_detections_never_null(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["detections"] is not None
        assert isinstance(body["detections"], list)

    def test_page_summaries_never_null(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["page_summaries"] is not None
        for ps in body["page_summaries"]:
            assert ps["class_counts"] is not None
            assert ps["classes_found"] is not None

    def test_artifacts_never_null(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["artifacts"] is not None
        for a in body["artifacts"]:
            assert a["type"] is not None
            assert a["path"] is not None

    def test_model_info_never_null(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["model"] is not None
        assert body["model"]["model_id"] is not None
        assert body["model"]["version"] is not None

    def test_summary_never_null(self, client, electrical_pdf):
        resp = _call_detect(client, electrical_pdf)
        body = resp.json()
        assert body["summary"] is not None
        assert body["summary"]["class_counts"] is not None


# =====================================================================
# Test: Tool registration
# =====================================================================

class TestToolRegistration:
    def test_detect_symbols_registered(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        names = [t["function"]["name"] for t in resp.json()["tools"]]
        assert "blueprint_detect_symbols" in names
        assert "blueprint_list_models" in names

    def test_detect_symbols_has_required_params(self, client):
        resp = client.get("/tools")
        tools_by_name = {
            t["function"]["name"]: t["function"] for t in resp.json()["tools"]
        }
        detect = tools_by_name["blueprint_detect_symbols"]
        assert "workspace" in detect["parameters"]["required"]
        assert "pdf_path" in detect["parameters"]["required"]

        models = tools_by_name["blueprint_list_models"]
        assert models["parameters"]["required"] == []
