import os, subprocess

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspaces")
RUNNER_CONTAINER = "agent-runner"

def _safe_workspace_path(workspace: str) -> str:
    # prevent path traversal
    if "/" in workspace or ".." in workspace:
        raise ValueError("Invalid workspace name")
    return os.path.join(WORKSPACE_ROOT, workspace)

def sandbox_run(workspace: str, cmd: str, timeout_sec: int = 600):
    ws = _safe_workspace_path(workspace)
    # run inside runner container at the workspace directory
    docker_cmd = [
        "docker", "exec",
        "-w", ws,
        RUNNER_CONTAINER,
        "bash", "-lc", cmd
    ]
    try:
        p = subprocess.run(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            text=True
        )
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout[-20000:],
            "stderr": p.stderr[-20000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout_sec}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
