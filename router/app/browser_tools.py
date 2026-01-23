import os, subprocess, textwrap

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspaces")
BROWSER_CONTAINER = "agent-browser"

def _ws(workspace: str) -> str:
    if "/" in workspace or ".." in workspace:
        raise ValueError("Invalid workspace")
    return os.path.join(WORKSPACE_ROOT, workspace)

def _exec_py(workspace: str, code: str):
    ws = _ws(workspace)
    cmd = [
        "docker", "exec",
        "-w", ws,
        BROWSER_CONTAINER,
        "python", "-c", code
    ]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        return {"ok": p.returncode == 0, "stdout": p.stdout[-20000:], "stderr": p.stderr[-20000:], "returncode": p.returncode}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _playwright_script(workspace: str, session: str, action_py: str):
    ws = _ws(workspace)
    # session dir under workspace for persistence
    return textwrap.dedent(f"""
    import os
    from playwright.sync_api import sync_playwright

    ws = r"{ws}"
    session = {session!r}
    os.makedirs(os.path.join(ws, ".browser", session), exist_ok=True)
    user_data_dir = os.path.join(ws, ".browser", session, "user_data")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        
        {action_py}
        
        browser.close()
    """)

def browser_goto(workspace: str, url: str, session: str = "default"):
    action = f"""
page.goto({url!r}, wait_until="domcontentloaded", timeout=60000)
print("ok")
print("title:", page.title())
print("url:", page.url)
"""
    script = _playwright_script(workspace, session, action)
    return _exec_py(workspace, script)

def browser_click(workspace: str, selector: str, session: str = "default"):
    action = f"""
page.click({selector!r}, timeout=30000)
print("ok")
"""
    script = _playwright_script(workspace, session, action)
    return _exec_py(workspace, script)

def browser_type(workspace: str, selector: str, text: str, session: str = "default", clear_first: bool = True):
    clear = "page.fill({selector!r}, '')" if clear_first else ""
    action = f"""
{clear}
page.type({selector!r}, {text!r}, timeout=30000)
print("ok")
"""
    script = _playwright_script(workspace, session, action)
    return _exec_py(workspace, script)

def browser_screenshot(workspace: str, path: str, session: str = "default"):
    ws = _ws(workspace)
    full_path = os.path.join(ws, path)
    action = f"""
import os
os.makedirs(os.path.dirname({full_path!r}), exist_ok=True)
page.screenshot(path={full_path!r}, full_page=True)
print("ok")
print("saved:", {full_path!r})
"""
    script = _playwright_script(workspace, session, action)
    return _exec_py(workspace, script)

def browser_extract_text(workspace: str, session: str = "default", selector: str = None):
    if selector:
        action = f"""
text = page.locator({selector!r}).inner_text(timeout=30000)
print("ok")
print(text)
"""
    else:
        action = """
text = page.inner_text("body", timeout=30000)
print("ok")
print(text)
"""
    script = _playwright_script(workspace, session, action)
    return _exec_py(workspace, script)
