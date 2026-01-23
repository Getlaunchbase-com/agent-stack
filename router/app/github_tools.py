import os
from .sandbox_tools import sandbox_run

GITHUB_DEFAULT_BRANCH = os.getenv("GITHUB_DEFAULT_BRANCH", "main")

def repo_commit(workspace: str, message: str, add_all: bool = True):
    if add_all:
        r1 = sandbox_run(workspace, "git add -A")
        if not r1["ok"]:
            return {"ok": False, "error": "git add failed", "details": r1}
    r2 = sandbox_run(workspace, f'git commit -m "{message.replace(chr(34), "")}"')
    # git commit returns non-zero if nothing to commit; treat as ok with note
    if not r2["ok"] and "nothing to commit" in (r2.get("stdout","") + r2.get("stderr","")).lower():
        return {"ok": True, "note": "nothing to commit", "details": r2}
    return {"ok": r2["ok"], "details": r2}

def repo_open_pr(workspace: str, title: str, body: str, head_branch: str, base_branch: str = None):
    if base_branch is None:
        base_branch = GITHUB_DEFAULT_BRANCH
    
    # assumes origin is set and token auth is configured in remote URL or via gh cli
    r1 = sandbox_run(workspace, f"git push -u origin {head_branch}")
    if not r1["ok"]:
        return {"ok": False, "error": "push failed", "details": r1}

    # Use GitHub API via requests
    owner = os.getenv("GITHUB_OWNER")
    repo = os.getenv("GITHUB_REPO")
    token = os.getenv("GITHUB_TOKEN")

    if not (owner and repo and token):
        return {"ok": False, "error": "Missing GITHUB_OWNER/GITHUB_REPO/GITHUB_TOKEN env vars"}

    import requests
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    payload = {"title": title, "body": body, "head": head_branch, "base": base_branch}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code >= 300:
            return {"ok": False, "error": "PR create failed", "status": resp.status_code, "response": resp.text}
        data = resp.json()
        return {"ok": True, "pr_url": data.get("html_url"), "number": data.get("number")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
