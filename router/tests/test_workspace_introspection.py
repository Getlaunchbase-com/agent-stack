"""Acceptance tests for PR2: workspace registry introspection + 422 structured errors."""

import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    """Create a temp WORKSPACE_ROOT with two sample workspaces."""
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    (ws_root / "proj-alpha").mkdir()
    (ws_root / "proj-beta").mkdir()
    # hidden dir should be excluded
    (ws_root / ".hidden").mkdir()
    # file at root level should be excluded (not a dir)
    (ws_root / "stray-file.txt").write_text("ignore me")

    monkeypatch.setenv("WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("ROUTER_AUTH_TOKEN", "")  # disable auth for tests

    # Force module reimport so WORKSPACE_ROOT picks up the env var
    import importlib
    from router.app import workspace_tools, tools, main

    importlib.reload(workspace_tools)
    importlib.reload(tools)
    importlib.reload(main)

    yield str(ws_root)


@pytest.fixture()
def client():
    from router.app.main import app
    return TestClient(app, raise_server_exceptions=False)


# =====================================================================
# Acceptance Test 1: workspace_list_roots returns at least one workspace
# =====================================================================

class TestWorkspaceListRoots:
    def test_returns_workspaces(self, client):
        """Calling workspace_list_roots returns at least one workspace."""
        resp = client.post("/tool", json={
            "tool_call": {"name": "workspace_list_roots", "arguments": {}}
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert len(body["workspaces"]) >= 1

    def test_returns_correct_ids(self, client, _workspace_root):
        """Returns both workspaces with correct IDs and root paths."""
        resp = client.post("/tool", json={
            "tool_call": {"name": "workspace_list_roots", "arguments": {}}
        })
        body = resp.json()
        ids = [w["id"] for w in body["workspaces"]]
        assert "proj-alpha" in ids
        assert "proj-beta" in ids
        # hidden dirs and files excluded
        assert ".hidden" not in ids
        assert "stray-file.txt" not in ids

    def test_each_entry_has_root_path(self, client, _workspace_root):
        """Each workspace entry includes a root path that exists on disk."""
        resp = client.post("/tool", json={
            "tool_call": {"name": "workspace_list_roots", "arguments": {}}
        })
        body = resp.json()
        for ws in body["workspaces"]:
            assert "id" in ws
            assert "root" in ws
            assert os.path.isdir(ws["root"])

    def test_read_only_no_side_effects(self, client, _workspace_root):
        """Calling workspace_list_roots multiple times is idempotent."""
        r1 = client.post("/tool", json={
            "tool_call": {"name": "workspace_list_roots", "arguments": {}}
        })
        r2 = client.post("/tool", json={
            "tool_call": {"name": "workspace_list_roots", "arguments": {}}
        })
        assert r1.json() == r2.json()

    def test_listed_in_tools_endpoint(self, client):
        """workspace_list_roots appears in the GET /tools schema."""
        resp = client.get("/tools")
        assert resp.status_code == 200
        names = [t["function"]["name"] for t in resp.json()["tools"]]
        assert "workspace_list_roots" in names


# =====================================================================
# Acceptance Test 2: invalid workspace returns 422 with structured payload
# =====================================================================

class TestStructured422:
    def test_workspace_list_invalid_returns_422(self, client):
        """workspace_list with invalid workspace returns 422 with availableWorkspaces."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "workspace_list",
                "arguments": {"workspace": "nonexistent-ws"}
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "workspace_list"
        assert detail["args"] == {"workspace": "nonexistent-ws"}
        assert "not found" in detail["reason"].lower()
        assert isinstance(detail["availableWorkspaces"], list)
        assert "proj-alpha" in detail["availableWorkspaces"]
        assert "proj-beta" in detail["availableWorkspaces"]

    def test_workspace_read_invalid_returns_422(self, client):
        """workspace_read with invalid workspace returns structured 422."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "workspace_read",
                "arguments": {"workspace": "ghost", "path": "file.txt"}
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "workspace_read"
        assert detail["reason"] == "Workspace 'ghost' not found"
        assert "proj-alpha" in detail["availableWorkspaces"]

    def test_workspace_write_invalid_returns_422(self, client):
        """workspace_write with invalid workspace returns structured 422."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "workspace_write",
                "arguments": {"workspace": "nope", "path": "x.txt", "content": "hi"}
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "workspace_write"
        assert "nope" in detail["reason"]
        assert len(detail["availableWorkspaces"]) >= 1

    def test_sandbox_run_invalid_returns_422(self, client):
        """sandbox_run with invalid workspace returns structured 422."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "sandbox_run",
                "arguments": {"workspace": "fake", "cmd": "ls"}
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "sandbox_run"
        assert "fake" in detail["reason"]

    def test_browser_goto_invalid_returns_422(self, client):
        """browser_goto with invalid workspace returns structured 422."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "browser_goto",
                "arguments": {"workspace": "doesnt-exist", "url": "https://example.com"}
            }
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["tool"] == "browser_goto"

    def test_valid_workspace_passes_through(self, client):
        """Valid workspace does NOT trigger 422; reaches the actual tool."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "workspace_list",
                "arguments": {"workspace": "proj-alpha"}
            }
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

    def test_422_payload_includes_args(self, client):
        """The 422 payload echoes back the exact args that were passed."""
        args = {"workspace": "bogus", "path": "some/file.txt", "max_bytes": 100}
        resp = client.post("/tool", json={
            "tool_call": {"name": "workspace_read", "arguments": args}
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["args"] == args

    def test_non_workspace_tool_unaffected(self, client):
        """Tools without workspace param (e.g. check_approval) are unaffected."""
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "check_approval",
                "arguments": {"approval_id": "does-not-exist"}
            }
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
