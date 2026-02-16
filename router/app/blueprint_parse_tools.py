"""Blueprint document parser (Tier-2) — the forever blueprint pipeline entry point.

Tool: blueprint_parse_document

Takes a blueprint PDF and produces a strict BlueprintParseV1 JSON envelope:
  - Page PNGs rendered at configurable DPI
  - Text blocks with coordinates (bbox, font metadata)
  - Legend region candidates (heuristic detection)
  - Debug artifacts (raw text dump, block overlay PNG)
  - Schema version tag for forward compatibility

Never returns null content — all fields are populated with stable defaults.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import fitz  # PyMuPDF

from .contracts.blueprint.validate_blueprint_parse import (
    build_contract_block,
    validate_or_error,
)
from .contracts.governance import get_vertex_stamp

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspaces")

# Schema version for forward compatibility
SCHEMA_VERSION = "BlueprintParseV1"

# Default rendering DPI for page PNGs
DEFAULT_DPI = 150

# Legend detection heuristics — keywords and spatial rules
_LEGEND_KEYWORDS = re.compile(
    r"\b(?:legend|symbol\s*(?:list|schedule|key)|"
    r"abbreviation|note\s*:?\s*legend|"
    r"device\s*(?:schedule|legend|list|key)|"
    r"drawing\s*(?:legend|symbols?)|"
    r"electrical\s*(?:legend|symbols?)|"
    r"low[\s\-]?voltage\s*(?:legend|symbols?))\b",
    re.IGNORECASE,
)

# Title block heuristics — typically bottom-right of the page
_TITLE_BLOCK_KEYWORDS = re.compile(
    r"\b(?:drawing\s*(?:no|number|#)|sheet\s*(?:no|number|#)|"
    r"scale\s*:|date\s*:|rev(?:ision)?\s*:|"
    r"project\s*(?:no|name)|"
    r"checked\s*(?:by)?|drawn\s*(?:by)?|"
    r"approved\s*(?:by)?)\b",
    re.IGNORECASE,
)


def _abs(workspace: str, path: str) -> str:
    """Resolve workspace-relative path with traversal protection."""
    if "/" in workspace or ".." in workspace:
        raise ValueError("Invalid workspace")
    base = os.path.join(WORKSPACE_ROOT, workspace)
    full = os.path.normpath(os.path.join(base, path))
    if not full.startswith(base):
        raise ValueError("Path traversal blocked")
    return full


def _render_page_png(
    page: fitz.Page,
    output_path: str,
    dpi: int = DEFAULT_DPI,
) -> dict:
    """Render a single page to PNG and return metadata."""
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat)
    pix.save(output_path)
    return {
        "path": output_path,
        "width_px": pix.width,
        "height_px": pix.height,
        "dpi": dpi,
        "size_bytes": os.path.getsize(output_path),
    }


def _extract_text_blocks(page: fitz.Page) -> list[dict]:
    """Extract all text blocks with coordinates and font metadata."""
    raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks: list[dict] = []

    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # text blocks only
            continue

        bbox = [round(v, 1) for v in block.get("bbox", (0, 0, 0, 0))]
        lines_data: list[dict] = []
        full_text_parts: list[str] = []

        for line in block.get("lines", []):
            line_bbox = [round(v, 1) for v in line.get("bbox", (0, 0, 0, 0))]
            spans: list[dict] = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                spans.append({
                    "text": text.strip(),
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 0), 1),
                    "flags": span.get("flags", 0),
                    "color": span.get("color", 0),
                    "bbox": [round(v, 1) for v in span.get("bbox", (0, 0, 0, 0))],
                })
                full_text_parts.append(text.strip())

            if spans:
                lines_data.append({
                    "bbox": line_bbox,
                    "spans": spans,
                })

        block_text = " ".join(full_text_parts)
        if not block_text.strip():
            continue

        blocks.append({
            "text": block_text,
            "bbox": bbox,
            "lines": lines_data,
            "line_count": len(lines_data),
        })

    return blocks


def _detect_legend_candidates(
    blocks: list[dict],
    page_width: float,
    page_height: float,
) -> list[dict]:
    """Heuristic legend region detection.

    Strategies:
      1. Text blocks containing legend keywords
      2. Clusters of short text entries with symbol-like patterns
      3. Regions in typical legend locations (right side, bottom)
    """
    candidates: list[dict] = []
    seen_bboxes: set[tuple] = set()

    for block in blocks:
        text = block["text"]
        bbox = tuple(block["bbox"])

        # Strategy 1: keyword match
        if _LEGEND_KEYWORDS.search(text):
            if bbox not in seen_bboxes:
                candidates.append({
                    "bbox": list(bbox),
                    "method": "keyword",
                    "confidence": 0.85,
                    "matched_text": text[:120],
                })
                seen_bboxes.add(bbox)
                continue

        # Strategy 2: symbol schedule pattern (tabular short entries)
        # Look for blocks with repeated short lines and symbol-like content
        lines = block.get("lines", [])
        if len(lines) >= 3:
            short_lines = [
                ln for ln in lines
                if all(len(s.get("text", "")) < 40 for s in ln.get("spans", []))
            ]
            if len(short_lines) >= 3 and len(short_lines) / max(len(lines), 1) > 0.6:
                # Check if it looks like a symbol list
                has_symbol_pattern = any(
                    re.search(r"[A-Z]{1,4}\s*[-–—]\s*\w", s.get("text", ""))
                    for ln in short_lines
                    for s in ln.get("spans", [])
                )
                if has_symbol_pattern and bbox not in seen_bboxes:
                    candidates.append({
                        "bbox": list(bbox),
                        "method": "tabular_pattern",
                        "confidence": 0.55,
                        "matched_text": text[:120],
                    })
                    seen_bboxes.add(bbox)

    # Strategy 3: spatial heuristic — right-side region with dense short text
    right_threshold = page_width * 0.65
    for block in blocks:
        bbox = tuple(block["bbox"])
        if bbox in seen_bboxes:
            continue
        # Block is in the right 35% of page
        if block["bbox"][0] >= right_threshold:
            lines = block.get("lines", [])
            if len(lines) >= 4:
                candidates.append({
                    "bbox": list(bbox),
                    "method": "spatial_right",
                    "confidence": 0.35,
                    "matched_text": block["text"][:120],
                })
                seen_bboxes.add(bbox)

    # Sort by confidence descending
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates


def _detect_title_block(
    blocks: list[dict],
    page_width: float,
    page_height: float,
) -> dict | None:
    """Detect the title block region (typically bottom-right)."""
    bottom_threshold = page_height * 0.70
    right_threshold = page_width * 0.50

    for block in blocks:
        bbox = block["bbox"]
        # Title block is usually in bottom-right quadrant
        if bbox[1] >= bottom_threshold and bbox[0] >= right_threshold:
            if _TITLE_BLOCK_KEYWORDS.search(block["text"]):
                return {
                    "bbox": bbox,
                    "text": block["text"][:200],
                    "confidence": 0.75,
                }

    # Fallback: just look for keyword matches anywhere
    for block in blocks:
        if _TITLE_BLOCK_KEYWORDS.search(block["text"]):
            keyword_count = len(_TITLE_BLOCK_KEYWORDS.findall(block["text"]))
            if keyword_count >= 2:
                return {
                    "bbox": block["bbox"],
                    "text": block["text"][:200],
                    "confidence": 0.50,
                }

    return None


def _write_debug_text_dump(
    pages_data: list[dict],
    output_path: str,
) -> str:
    """Write a raw text dump of all pages for debugging."""
    with open(output_path, "w", encoding="utf-8") as f:
        for page_data in pages_data:
            f.write(f"=== PAGE {page_data['page_number']} ===\n")
            f.write(f"Size: {page_data['width_pt']}x{page_data['height_pt']} pt\n")
            f.write(f"Blocks: {page_data['block_count']}\n")
            f.write(f"Legend candidates: {len(page_data.get('legend_candidates', []))}\n")
            f.write("-" * 60 + "\n")
            for block in page_data.get("text_blocks", []):
                bbox = block["bbox"]
                f.write(f"  [{bbox[0]:.0f},{bbox[1]:.0f} → {bbox[2]:.0f},{bbox[3]:.0f}] ")
                f.write(block["text"][:100])
                f.write("\n")
            f.write("\n")
    return output_path


def _write_debug_overlay_png(
    page: fitz.Page,
    blocks: list[dict],
    legend_candidates: list[dict],
    title_block: dict | None,
    output_path: str,
    dpi: int = DEFAULT_DPI,
) -> str:
    """Render a debug PNG with block outlines and legend regions highlighted."""
    # Render page at target DPI
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat)

    # We'll draw annotations directly on the fitz page copy, then render
    # Create a temporary copy of the page's parent doc for annotation
    temp_doc = fitz.open()
    temp_doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    temp_page = temp_doc[0]

    # Draw text block outlines in blue
    for block in blocks:
        bbox = block["bbox"]
        rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
        annot = temp_page.draw_rect(rect, color=(0, 0, 1), width=0.5)

    # Draw legend candidates in green
    for lc in legend_candidates:
        bbox = lc["bbox"]
        rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
        temp_page.draw_rect(rect, color=(0, 0.8, 0), width=2.0)

    # Draw title block in red
    if title_block:
        bbox = title_block["bbox"]
        rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
        temp_page.draw_rect(rect, color=(1, 0, 0), width=2.0)

    # Render annotated page
    pix = temp_page.get_pixmap(matrix=mat)
    pix.save(output_path)
    temp_doc.close()
    return output_path


# ===================================================================
# Public tool function
# ===================================================================

def blueprint_parse_document(
    workspace: str,
    pdf_path: str,
    dpi: int = DEFAULT_DPI,
    output_dir: str = "artifacts/parse",
    include_debug: bool = True,
) -> dict:
    """Parse a blueprint PDF into structured BlueprintParseV1 output.

    Produces:
      - Page PNGs rendered at target DPI
      - Text blocks with coordinates, font metadata
      - Legend region candidates (heuristic)
      - Title block detection
      - Debug artifacts (text dump, block overlay PNGs)

    Args:
        workspace: Workspace folder ID.
        pdf_path: Relative path to PDF inside workspace.
        dpi: Render resolution for page PNGs (default 150).
        output_dir: Output directory for artifacts (relative to workspace).
        include_debug: Whether to produce debug overlay PNGs and text dump.

    Returns:
        Strict BlueprintParseV1 JSON envelope. Never null content.
    """
    full_pdf = _abs(workspace, pdf_path)
    if not os.path.isfile(full_pdf):
        return {"ok": False, "error": f"PDF not found: {pdf_path}"}

    try:
        doc = fitz.open(full_pdf)
    except Exception as e:
        return {"ok": False, "error": f"Failed to open PDF: {e}"}

    # Prepare output directories
    out_base = _abs(workspace, output_dir)
    png_dir = os.path.join(out_base, "pages")
    debug_dir = os.path.join(out_base, "debug")
    os.makedirs(png_dir, exist_ok=True)
    if include_debug:
        os.makedirs(debug_dir, exist_ok=True)

    parse_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    pages_data: list[dict] = []
    all_artifacts: list[dict] = []
    total_blocks = 0
    total_legend_candidates = 0

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num = page_idx + 1
        width_pt = round(page.rect.width, 2)
        height_pt = round(page.rect.height, 2)

        # 1. Render page PNG
        png_filename = f"page_{page_num:03d}.png"
        png_path = os.path.join(png_dir, png_filename)
        png_meta = _render_page_png(page, png_path, dpi=dpi)
        all_artifacts.append({
            "type": "page_png",
            "page": page_num,
            "path": os.path.relpath(png_path, _abs(workspace, "")),
            "size_bytes": png_meta["size_bytes"],
        })

        # 2. Extract text blocks with coords
        text_blocks = _extract_text_blocks(page)
        total_blocks += len(text_blocks)

        # 3. Detect legend region candidates
        legend_candidates = _detect_legend_candidates(
            text_blocks, width_pt, height_pt
        )
        total_legend_candidates += len(legend_candidates)

        # 4. Detect title block
        title_block = _detect_title_block(text_blocks, width_pt, height_pt)

        # Build page data
        page_entry = {
            "page_number": page_num,
            "width_pt": width_pt,
            "height_pt": height_pt,
            "width_px": png_meta["width_px"],
            "height_px": png_meta["height_px"],
            "dpi": dpi,
            "png_path": os.path.relpath(png_path, _abs(workspace, "")),
            "block_count": len(text_blocks),
            "text_blocks": text_blocks,
            "legend_candidates": legend_candidates,
            "title_block": title_block if title_block else {
                "bbox": [0, 0, 0, 0],
                "text": "",
                "confidence": 0.0,
            },
        }
        pages_data.append(page_entry)

        # 5. Debug overlay PNG
        if include_debug:
            overlay_filename = f"debug_overlay_{page_num:03d}.png"
            overlay_path = os.path.join(debug_dir, overlay_filename)
            _write_debug_overlay_png(
                page, text_blocks, legend_candidates, title_block,
                overlay_path, dpi=dpi,
            )
            all_artifacts.append({
                "type": "debug_overlay",
                "page": page_num,
                "path": os.path.relpath(overlay_path, _abs(workspace, "")),
                "size_bytes": os.path.getsize(overlay_path),
            })

    # Write debug text dump
    if include_debug:
        text_dump_path = os.path.join(debug_dir, "raw_text_dump.txt")
        _write_debug_text_dump(pages_data, text_dump_path)
        all_artifacts.append({
            "type": "debug_text_dump",
            "path": os.path.relpath(text_dump_path, _abs(workspace, "")),
            "size_bytes": os.path.getsize(text_dump_path),
        })

    # Write the full parse JSON to disk as an artifact
    parse_json_path = os.path.join(out_base, "parse_result.json")

    doc.close()

    # Build the BlueprintParseV1 envelope
    result = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "parse_id": parse_id,
        "generated_at": now,
        "pdf_path": pdf_path,
        "page_count": len(pages_data),
        "total_blocks": total_blocks,
        "total_legend_candidates": total_legend_candidates,
        "dpi": dpi,
        "contract": build_contract_block(model_version=None),
        "errors": [],
        "pages": pages_data,
        "artifacts": all_artifacts,
    }

    # Validate against frozen schema before returning
    validation_error = validate_or_error(result)
    if validation_error is not None:
        logger.error("BlueprintParseV1 validation failed: %s", validation_error)
        return validation_error

    # Persist the parse result to disk (schema-valid payload)
    with open(parse_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    result["artifacts"].append({
        "type": "parse_json",
        "path": os.path.relpath(parse_json_path, _abs(workspace, "")),
        "size_bytes": os.path.getsize(parse_json_path),
    })

    # Vertex stamp — freeze metadata for governance traceability.
    # Injected AFTER schema validation (schema is frozen, no additionalProperties).
    result["vertex"] = get_vertex_stamp()

    return result
