import os

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspaces")

def _abs(workspace: str, path: str) -> str:
    if "/" in workspace or ".." in workspace:
        raise ValueError("Invalid workspace")
    base = os.path.join(WORKSPACE_ROOT, workspace)
    full = os.path.normpath(os.path.join(base, path))
    if not full.startswith(base):
        raise ValueError("Path traversal blocked")
    return full

def workspace_list(workspace: str, path: str = "."):
    full = _abs(workspace, path)
    if not os.path.exists(full):
        return {"ok": False, "error": "path not found"}
    items = []
    for name in sorted(os.listdir(full)):
        p = os.path.join(full, name)
        items.append({
            "name": name,
            "is_dir": os.path.isdir(p),
            "size": os.path.getsize(p) if os.path.isfile(p) else None
        })
    return {"ok": True, "path": path, "items": items}

def workspace_read(workspace: str, path: str, max_bytes: int = 200000):
    full = _abs(workspace, path)
    if not os.path.isfile(full):
        return {"ok": False, "error": "file not found"}
    data = open(full, "rb").read(max_bytes + 1)
    if len(data) > max_bytes:
        return {"ok": False, "error": f"file too large > {max_bytes} bytes"}
    return {"ok": True, "path": path, "content": data.decode("utf-8", errors="replace")}

def workspace_write(workspace: str, path: str, content: str, mkdirs: bool = True):
    full = _abs(workspace, path)
    d = os.path.dirname(full)
    if mkdirs:
        os.makedirs(d, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}
