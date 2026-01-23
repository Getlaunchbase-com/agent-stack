from .approvals import request_approval, check_approval
from .sandbox_tools import sandbox_run
from .workspace_tools import workspace_list, workspace_read, workspace_write
from .github_tools import repo_commit, repo_open_pr
from .browser_tools import browser_goto, browser_click, browser_type, browser_screenshot, browser_extract_text

TOOL_MAP = {
    "request_approval": request_approval,
    "check_approval": check_approval,
    "sandbox_run": sandbox_run,
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

def dispatch_tool_call(name: str, arguments: dict):
    if name not in TOOL_MAP:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return TOOL_MAP[name](**arguments)
    except Exception as e:
        return {"ok": False, "error": str(e)}
