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
}


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
    if name in _WORKSPACE_TOOLS:
        _validate_workspace(name, arguments)
    try:
        return TOOL_MAP[name](**arguments)
    except HTTPException:
        raise  # re-raise HTTP exceptions so FastAPI handles them
    except Exception as e:
        return {"ok": False, "error": str(e)}
