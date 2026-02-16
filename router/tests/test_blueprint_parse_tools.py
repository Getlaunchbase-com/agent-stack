"""Acceptance tests for PR-A1: blueprint_parse_document.

Validates:
  - Strict BlueprintParseV1 schema on all outputs
  - Page PNGs produced on disk
  - Text blocks have coords (bbox) and font metadata
  - Legend region candidates detected where present
  - Debug artifacts produced (overlay PNGs, text dump)
  - Never returns null content — stable empty defaults
  - Runs against 3 sample PDFs with stable outputs
  - No 4xx/422 drift (workspace validation works)
  - Tool registered in /tools endpoint
"""

import importlib
import json
import os

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient


# ---- Sample PDF builders ----

def _create_electrical_pdf(ws_dir: str, filename: str = "plans/electrical.pdf") -> str:
    """Sample 1: Electrical plan with legend and title block."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()

    # Page 1: Main electrical plan with legend
    page1 = doc.new_page(width=792, height=612)
    page1.insert_text(
        (72, 72),
        "FIRST FLOOR ELECTRICAL PLAN\nDrawing: E-101\nScale: 1/8\" = 1'-0\"",
        fontsize=14,
    )
    page1.insert_text(
        (72, 150),
        "OPEN OFFICE AREA\n"
        "24x Cat6A Data Drop\n"
        "6x Wireless AP (ceiling mount)\n"
        "4x CCTV IP Camera\n",
        fontsize=10,
    )
    # Legend block on the right side
    page1.insert_text(
        (550, 80),
        "LEGEND\n"
        "AP - Access Point\n"
        "CAM - IP Camera\n"
        "DD - Data Drop\n"
        "CR - Card Reader\n"
        "SD - Smoke Detector\n",
        fontsize=8,
    )
    # Title block bottom-right
    page1.insert_text(
        (500, 520),
        "Project Name: Test Building\n"
        "Drawing No: E-101\n"
        "Sheet No: 1 of 3\n"
        "Scale: 1/8\" = 1'-0\"\n"
        "Date: 2024-01-15\n"
        "Drawn By: JD\n"
        "Checked By: SM\n",
        fontsize=7,
    )

    # Page 2: Security plan
    page2 = doc.new_page(width=792, height=612)
    page2.insert_text(
        (72, 72),
        "FIRST FLOOR SECURITY PLAN\nDrawing: E-102",
        fontsize=14,
    )
    page2.insert_text(
        (72, 140),
        "MAIN ENTRANCE\n"
        "2x Card Reader (HID iClass SE)\n"
        "2x Door Contact\n"
        "1x Intercom Station\n",
        fontsize=10,
    )
    page2.insert_text(
        (72, 260),
        "FIRE ALARM DEVICES\n"
        "8x Smoke Detector\n"
        "4x Horn/Strobe\n"
        "2x Pull Station\n",
        fontsize=10,
    )

    doc.save(full)
    doc.close()
    return filename


def _create_mechanical_pdf(ws_dir: str, filename: str = "plans/mechanical.pdf") -> str:
    """Sample 2: Mechanical plan with device schedule."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()

    page1 = doc.new_page(width=1224, height=792)  # D-size (36x24)
    page1.insert_text(
        (72, 72),
        "HVAC MECHANICAL PLAN — FLOOR 2\nDrawing: M-201",
        fontsize=16,
    )
    page1.insert_text(
        (72, 150),
        "AHU-1 Supply Ductwork\n"
        "VAV-2A through VAV-2H\n"
        "Return Air Plenum\n"
        "Exhaust Fan EF-2A, EF-2B\n",
        fontsize=11,
    )
    # Device schedule (legend variant)
    page1.insert_text(
        (800, 100),
        "DEVICE SCHEDULE\n"
        "AHU - Air Handling Unit\n"
        "VAV - Variable Air Volume\n"
        "EF - Exhaust Fan\n"
        "RTU - Rooftop Unit\n"
        "FCU - Fan Coil Unit\n",
        fontsize=9,
    )
    # Title block
    page1.insert_text(
        (900, 680),
        "Project No: 2024-M-001\n"
        "Drawing No: M-201\n"
        "Revision: A\n"
        "Date: 2024-02-20\n"
        "Approved By: KL\n",
        fontsize=7,
    )

    doc.save(full)
    doc.close()
    return filename


def _create_multipage_pdf(ws_dir: str, filename: str = "plans/multipage.pdf") -> str:
    """Sample 3: Multi-page plan (4 pages) with various content."""
    full = os.path.join(ws_dir, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)

    doc = fitz.open()

    # Page 1: Cover sheet
    p1 = doc.new_page(width=792, height=612)
    p1.insert_text((200, 250), "PROJECT DRAWINGS", fontsize=24)
    p1.insert_text((200, 290), "Test Commercial Building\nPhase 1", fontsize=16)
    p1.insert_text(
        (500, 520),
        "Sheet No: Cover\nDate: 2024-03-01\nDrawn By: AB",
        fontsize=8,
    )

    # Page 2: Site plan
    p2 = doc.new_page(width=792, height=612)
    p2.insert_text((72, 72), "SITE PLAN\nDrawing: C-001", fontsize=14)
    p2.insert_text(
        (72, 140),
        "Property boundary\nSetback lines\nParking layout (42 spaces)\n"
        "Storm drainage\nFire lane access\n",
        fontsize=10,
    )

    # Page 3: Electrical with symbol legend
    p3 = doc.new_page(width=792, height=612)
    p3.insert_text((72, 72), "ELECTRICAL PLAN - FLOOR 1\nDrawing: E-001", fontsize=14)
    p3.insert_text(
        (72, 140),
        "Panel Schedule PP-1A\n"
        "12x Duplex Outlet\n"
        "4x GFCI Outlet\n"
        "8x Recessed Light\n",
        fontsize=10,
    )
    p3.insert_text(
        (560, 80),
        "SYMBOL LIST\n"
        "DO - Duplex Outlet\n"
        "GF - GFCI Outlet\n"
        "RL - Recessed Light\n"
        "SW - Wall Switch\n"
        "JP - Junction Panel\n",
        fontsize=8,
    )

    # Page 4: Low voltage
    p4 = doc.new_page(width=792, height=612)
    p4.insert_text((72, 72), "LOW VOLTAGE PLAN\nDrawing: E-002", fontsize=14)
    p4.insert_text(
        (72, 140),
        "16x Cat6A Data Drop\n"
        "4x Wireless AP\n"
        "2x CCTV Camera\n"
        "1x MDF Cabinet\n",
        fontsize=10,
    )
    p4.insert_text(
        (500, 520),
        "Drawing No: E-002\nSheet No: 4 of 4\nScale: NTS\nDate: 2024-03-01",
        fontsize=7,
    )

    doc.save(full)
    doc.close()
    return filename


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    """Set up temp WORKSPACE_ROOT with a sample workspace."""
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    ws_dir = ws_root / "bp-project"
    ws_dir.mkdir()

    monkeypatch.setenv("WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("ROUTER_AUTH_TOKEN", "")

    from router.app import workspace_tools, blueprint_parse_tools, tools, main
    importlib.reload(blueprint_parse_tools)
    importlib.reload(workspace_tools)
    importlib.reload(tools)
    importlib.reload(main)

    yield str(ws_root)


@pytest.fixture()
def ws_dir(_workspace_root):
    return os.path.join(_workspace_root, "bp-project")


@pytest.fixture()
def client():
    from router.app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def electrical_pdf(ws_dir):
    return _create_electrical_pdf(ws_dir)


@pytest.fixture()
def mechanical_pdf(ws_dir):
    return _create_mechanical_pdf(ws_dir)


@pytest.fixture()
def multipage_pdf(ws_dir):
    return _create_multipage_pdf(ws_dir)


def _call_parse(client, pdf_path, **extra):
    args = {"workspace": "bp-project", "pdf_path": pdf_path}
    args.update(extra)
    return client.post("/tool", json={
        "tool_call": {"name": "blueprint_parse_document", "arguments": args}
    })


# =====================================================================
# Test: BlueprintParseV1 schema stability
# =====================================================================

class TestBlueprintParseV1Schema:
    """Every output conforms to the strict BlueprintParseV1 envelope."""

    REQUIRED_TOP_KEYS = {
        "ok", "schema_version", "parse_id", "generated_at",
        "pdf_path", "page_count", "total_blocks",
        "total_legend_candidates", "dpi", "pages", "artifacts",
    }

    REQUIRED_PAGE_KEYS = {
        "page_number", "width_pt", "height_pt", "width_px", "height_px",
        "dpi", "png_path", "block_count", "text_blocks",
        "legend_candidates", "title_block",
    }

    REQUIRED_BLOCK_KEYS = {"text", "bbox", "lines", "line_count"}

    def test_electrical_schema(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        assert resp.status_code == 200
        body = resp.json()

        assert body["ok"] is True
        assert body["schema_version"] == "BlueprintParseV1"
        assert self.REQUIRED_TOP_KEYS.issubset(set(body.keys()))
        assert isinstance(body["pages"], list)
        assert isinstance(body["artifacts"], list)
        assert body["page_count"] == 2

        for page in body["pages"]:
            assert self.REQUIRED_PAGE_KEYS.issubset(set(page.keys()))
            assert isinstance(page["text_blocks"], list)
            assert isinstance(page["legend_candidates"], list)
            assert isinstance(page["title_block"], dict)
            # Title block always has these keys (never null)
            assert "bbox" in page["title_block"]
            assert "text" in page["title_block"]
            assert "confidence" in page["title_block"]

            for block in page["text_blocks"]:
                assert self.REQUIRED_BLOCK_KEYS.issubset(set(block.keys()))
                assert isinstance(block["bbox"], list)
                assert len(block["bbox"]) == 4
                assert isinstance(block["text"], str)
                assert len(block["text"]) > 0

    def test_mechanical_schema(self, client, mechanical_pdf):
        resp = _call_parse(client, mechanical_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["schema_version"] == "BlueprintParseV1"
        assert self.REQUIRED_TOP_KEYS.issubset(set(body.keys()))
        assert body["page_count"] == 1

    def test_multipage_schema(self, client, multipage_pdf):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 4
        assert len(body["pages"]) == 4
        for page in body["pages"]:
            assert self.REQUIRED_PAGE_KEYS.issubset(set(page.keys()))


# =====================================================================
# Test: Page PNGs produced on disk
# =====================================================================

class TestPagePNGs:
    def test_pngs_created_for_each_page(self, client, electrical_pdf, ws_dir):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        assert body["ok"] is True

        png_artifacts = [a for a in body["artifacts"] if a["type"] == "page_png"]
        assert len(png_artifacts) == 2  # 2-page PDF

        for artifact in png_artifacts:
            full_path = os.path.join(ws_dir, artifact["path"])
            assert os.path.isfile(full_path), f"PNG not found: {full_path}"
            assert artifact["size_bytes"] > 0

    def test_png_dimensions_match_page(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()

        for page in body["pages"]:
            assert page["width_px"] > 0
            assert page["height_px"] > 0
            assert page["dpi"] == 150  # default DPI

    def test_custom_dpi(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf, dpi=72)
        body = resp.json()
        assert body["dpi"] == 72
        for page in body["pages"]:
            assert page["dpi"] == 72

    def test_multipage_pngs(self, client, multipage_pdf, ws_dir):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        png_artifacts = [a for a in body["artifacts"] if a["type"] == "page_png"]
        assert len(png_artifacts) == 4
        for artifact in png_artifacts:
            full_path = os.path.join(ws_dir, artifact["path"])
            assert os.path.isfile(full_path)


# =====================================================================
# Test: Text blocks with coords
# =====================================================================

class TestTextBlocks:
    def test_blocks_have_coordinates(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        page1 = body["pages"][0]
        assert page1["block_count"] > 0
        assert len(page1["text_blocks"]) > 0

        for block in page1["text_blocks"]:
            bbox = block["bbox"]
            assert len(bbox) == 4
            assert all(isinstance(v, (int, float)) for v in bbox)
            # x0 < x1 and y0 < y1
            assert bbox[2] >= bbox[0]
            assert bbox[3] >= bbox[1]

    def test_blocks_have_font_metadata(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        page1 = body["pages"][0]
        # At least one block should have line/span detail
        block = page1["text_blocks"][0]
        assert "lines" in block
        assert block["line_count"] >= 1
        line = block["lines"][0]
        assert "spans" in line
        span = line["spans"][0]
        assert "font" in span
        assert "size" in span
        assert "bbox" in span

    def test_total_blocks_aggregate(self, client, multipage_pdf):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        expected = sum(p["block_count"] for p in body["pages"])
        assert body["total_blocks"] == expected


# =====================================================================
# Test: Legend region candidates
# =====================================================================

class TestLegendCandidates:
    def test_detects_legend_keyword(self, client, electrical_pdf):
        """Electrical PDF has explicit LEGEND text — should be detected."""
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        page1 = body["pages"][0]
        assert len(page1["legend_candidates"]) >= 1
        best = page1["legend_candidates"][0]
        assert best["confidence"] > 0
        assert "bbox" in best
        assert "method" in best
        assert "matched_text" in best

    def test_detects_device_schedule(self, client, mechanical_pdf):
        """Mechanical PDF has DEVICE SCHEDULE — should be detected as legend."""
        resp = _call_parse(client, mechanical_pdf)
        body = resp.json()
        page1 = body["pages"][0]
        assert len(page1["legend_candidates"]) >= 1

    def test_detects_symbol_list(self, client, multipage_pdf):
        """Multipage PDF page 3 has SYMBOL LIST — should be detected."""
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        # Page 3 (index 2) has "SYMBOL LIST"
        page3 = body["pages"][2]
        assert len(page3["legend_candidates"]) >= 1
        methods = {lc["method"] for lc in page3["legend_candidates"]}
        assert "keyword" in methods

    def test_total_legend_candidates_aggregate(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        expected = sum(len(p["legend_candidates"]) for p in body["pages"])
        assert body["total_legend_candidates"] == expected

    def test_legend_candidates_always_list(self, client, multipage_pdf):
        """Even pages without legends return empty list, never null."""
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        for page in body["pages"]:
            assert isinstance(page["legend_candidates"], list)


# =====================================================================
# Test: Debug artifacts
# =====================================================================

class TestDebugArtifacts:
    def test_debug_overlays_produced(self, client, electrical_pdf, ws_dir):
        resp = _call_parse(client, electrical_pdf, include_debug=True)
        body = resp.json()
        overlays = [a for a in body["artifacts"] if a["type"] == "debug_overlay"]
        assert len(overlays) == 2  # one per page
        for overlay in overlays:
            full_path = os.path.join(ws_dir, overlay["path"])
            assert os.path.isfile(full_path)
            assert overlay["size_bytes"] > 0

    def test_debug_text_dump_produced(self, client, electrical_pdf, ws_dir):
        resp = _call_parse(client, electrical_pdf, include_debug=True)
        body = resp.json()
        text_dumps = [a for a in body["artifacts"] if a["type"] == "debug_text_dump"]
        assert len(text_dumps) == 1
        full_path = os.path.join(ws_dir, text_dumps[0]["path"])
        assert os.path.isfile(full_path)
        content = open(full_path).read()
        assert "PAGE 1" in content
        assert "PAGE 2" in content

    def test_parse_json_artifact_produced(self, client, electrical_pdf, ws_dir):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        json_artifacts = [a for a in body["artifacts"] if a["type"] == "parse_json"]
        assert len(json_artifacts) == 1
        full_path = os.path.join(ws_dir, json_artifacts[0]["path"])
        assert os.path.isfile(full_path)
        with open(full_path) as f:
            parsed = json.load(f)
        assert parsed["schema_version"] == "BlueprintParseV1"

    def test_no_debug_when_disabled(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf, include_debug=False)
        body = resp.json()
        overlays = [a for a in body["artifacts"] if a["type"] == "debug_overlay"]
        text_dumps = [a for a in body["artifacts"] if a["type"] == "debug_text_dump"]
        assert len(overlays) == 0
        assert len(text_dumps) == 0


# =====================================================================
# Test: Never null content
# =====================================================================

class TestNeverNullContent:
    def test_title_block_never_null(self, client, multipage_pdf):
        """Pages without title blocks get default dict, not null."""
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        for page in body["pages"]:
            tb = page["title_block"]
            assert tb is not None
            assert isinstance(tb, dict)
            assert "bbox" in tb
            assert "text" in tb
            assert "confidence" in tb

    def test_legend_candidates_never_null(self, client, multipage_pdf):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        for page in body["pages"]:
            assert page["legend_candidates"] is not None
            assert isinstance(page["legend_candidates"], list)

    def test_text_blocks_never_null(self, client, multipage_pdf):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        for page in body["pages"]:
            assert page["text_blocks"] is not None
            assert isinstance(page["text_blocks"], list)

    def test_missing_pdf_returns_error_json(self, client):
        resp = _call_parse(client, "nonexistent.pdf")
        body = resp.json()
        assert body is not None
        assert body["ok"] is False
        assert "not found" in body["error"].lower()

    def test_artifacts_list_never_null(self, client, electrical_pdf):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        assert body["artifacts"] is not None
        assert isinstance(body["artifacts"], list)
        for a in body["artifacts"]:
            assert a["type"] is not None
            assert a["path"] is not None


# =====================================================================
# Test: 3 sample PDFs produce stable outputs
# =====================================================================

class TestThreeSamplePDFs:
    """Run against all 3 sample PDFs and verify stable, consistent outputs."""

    def test_electrical_produces_stable_output(self, client, electrical_pdf, ws_dir):
        resp = _call_parse(client, electrical_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 2
        assert body["total_blocks"] > 0
        # Page PNGs exist
        for page in body["pages"]:
            png_path = os.path.join(ws_dir, page["png_path"])
            assert os.path.isfile(png_path)
        # Artifacts are consistent
        types = {a["type"] for a in body["artifacts"]}
        assert "page_png" in types
        assert "parse_json" in types

    def test_mechanical_produces_stable_output(self, client, mechanical_pdf, ws_dir):
        resp = _call_parse(client, mechanical_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 1
        assert body["total_blocks"] > 0
        png_path = os.path.join(ws_dir, body["pages"][0]["png_path"])
        assert os.path.isfile(png_path)

    def test_multipage_produces_stable_output(self, client, multipage_pdf, ws_dir):
        resp = _call_parse(client, multipage_pdf)
        body = resp.json()
        assert body["ok"] is True
        assert body["page_count"] == 4
        assert len(body["pages"]) == 4
        assert body["total_blocks"] > 0
        for page in body["pages"]:
            png_path = os.path.join(ws_dir, page["png_path"])
            assert os.path.isfile(png_path)


# =====================================================================
# Test: No 4xx/422 drift (workspace validation)
# =====================================================================

class TestWorkspaceValidation:
    def test_invalid_workspace_422(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "blueprint_parse_document",
                "arguments": {"workspace": "nonexistent", "pdf_path": "x.pdf"},
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "blueprint_parse_document"
        assert "availableWorkspaces" in detail


# =====================================================================
# Test: Tool registration
# =====================================================================

class TestToolRegistration:
    def test_registered_in_tools_endpoint(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        names = [t["function"]["name"] for t in resp.json()["tools"]]
        assert "blueprint_parse_document" in names

    def test_has_required_params(self, client):
        resp = client.get("/tools")
        tools_by_name = {
            t["function"]["name"]: t["function"] for t in resp.json()["tools"]
        }
        parse = tools_by_name["blueprint_parse_document"]
        assert "workspace" in parse["parameters"]["required"]
        assert "pdf_path" in parse["parameters"]["required"]
