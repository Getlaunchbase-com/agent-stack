from fastapi import HTTPException

from .approvals import request_approval, check_approval
from .sandbox_tools import sandbox_run
from .workspace_tools import (
    workspace_list,
    workspace_list_roots,
    workspace_read,
    workspace_write,
    get_available_workspaces,
)
from .github_tools import repo_commit, repo_open_pr
from .browser_tools import (
    browser_goto,
    browser_click,
    browser_type,
    browser_screenshot,
    browser_extract_text,
)
from .blueprint_tools import (
    blueprint_extract_text,
    blueprint_takeoff_low_voltage,
    artifact_write_xlsx_takeoff,
    artifact_write_docx_summary,
)
from .vendor_pricing_tools import (
    vendor_price_search,
    vendor_price_check,
    vendor_list_sources,
)
from .blueprint_parse_tools import blueprint_parse_document
from .blueprint_detect_tools import blueprint_detect_symbols, blueprint_list_models
from .contracts.governance import stamp_response, get_locked_contracts, is_frozen
from .contracts.contract_handshake import is_handshake_valid, handshake_status

TOOL_MAP = {
    "request_approval": request_approval,
    "check_approval": check_approval,
    "sandbox_run": sandbox_run,
    "workspace_list_roots": workspace_list_roots,
    "workspace_list": workspace_list,
    "workspace_read": workspace_read,
    "workspace_write": workspace_write,
    "repo_commit": repo_commit,
    "repo_open_pr": repo_open_pr,
    "browser_goto": browser_goto,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_screenshot": browser_screenshot,
    "browser_extract_text": browser_extract_text,
    "blueprint_extract_text": blueprint_extract_text,
    "blueprint_takeoff_low_voltage": blueprint_takeoff_low_voltage,
    "artifact_write_xlsx_takeoff": artifact_write_xlsx_takeoff,
    "artifact_write_docx_summary": artifact_write_docx_summary,
    "vendor_price_search": vendor_price_search,
    "vendor_price_check": vendor_price_check,
    "vendor_list_sources": vendor_list_sources,
    "blueprint_parse_document": blueprint_parse_document,
    "blueprint_detect_symbols": blueprint_detect_symbols,
    "blueprint_list_models": blueprint_list_models,
}

# Tools that accept a `workspace` argument and require it to exist on disk.
_WORKSPACE_TOOLS = {
    "sandbox_run",
    "workspace_list",
    "workspace_read",
    "workspace_write",
    "repo_commit",
    "repo_open_pr",
    "browser_goto",
    "browser_click",
    "browser_type",
    "browser_screenshot",
    "browser_extract_text",
    "blueprint_extract_text",
    "blueprint_takeoff_low_voltage",
    "artifact_write_xlsx_takeoff",
    "artifact_write_docx_summary",
    "blueprint_parse_document",
    "blueprint_detect_symbols",
}


# Tools that operate under frozen contracts and require a valid handshake.
# These will return 503 if the startup handshake with the platform failed.
_FROZEN_TOOLS = {
    "blueprint_extract_text",
    "blueprint_takeoff_low_voltage",
    "artifact_write_xlsx_takeoff",
    "artifact_write_docx_summary",
    "blueprint_parse_document",
    "blueprint_detect_symbols",
    "blueprint_list_models",
    "vendor_price_search",
    "vendor_price_check",
    "vendor_list_sources",
}


def _enforce_contract_handshake(tool_name: str) -> None:
    """Raise HTTP 503 if a frozen tool is called without a valid handshake."""
    if tool_name not in _FROZEN_TOOLS:
        return
    if not is_frozen():
        return
    if is_handshake_valid():
        return
    status = handshake_status()
    raise HTTPException(
        status_code=503,
        detail={
            "error": "CONTRACT_MISMATCH",
            "tool": tool_name,
            "reason": (
                "Contract handshake with platform failed or was not performed. "
                "Frozen tools will not dispatch until contracts are verified."
            ),
            "handshake_errors": status["errors"],
        },
    )


def _validate_workspace(tool_name: str, arguments: dict) -> None:
    """Raise HTTP 422 with structured payload if the workspace does not exist."""
    workspace = arguments.get("workspace")
    if workspace is None:
        return  # let downstream handle missing required param
    available = get_available_workspaces()
    ids = [w["id"] for w in available]
    if workspace not in ids:
        raise HTTPException(
            status_code=422,
            detail={
                "tool": tool_name,
                "args": arguments,
                "reason": f"Workspace '{workspace}' not found",
                "availableWorkspaces": ids,
            },
        )


def dispatch_tool_call(name: str, arguments: dict):
    if name not in TOOL_MAP:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    _enforce_contract_handshake(name)
    if name in _WORKSPACE_TOOLS:
        _validate_workspace(name, arguments)
    try:
        result = TOOL_MAP[name](**arguments)
        return stamp_response(result) if isinstance(result, dict) else result
    except HTTPException:
        raise  # re-raise HTTP exceptions so FastAPI handles them
    except Exception as e:
        return {"ok": False, "error": str(e)}
