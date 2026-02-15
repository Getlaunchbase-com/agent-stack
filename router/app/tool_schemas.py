TOOLS = [
  # ---- approvals ----
  {
    "type": "function",
    "function": {
      "name": "request_approval",
      "description": "Ask the human for approval before performing a gated action. Returns an approval_id to poll.",
      "parameters": {
        "type": "object",
        "properties": {
          "action": {"type": "string", "description": "Short action name, e.g. 'merge_pr'"},
          "summary": {"type": "string", "description": "One-paragraph explanation of what will happen"},
          "risk": {"type": "string", "enum": ["low", "medium", "high"]},
          "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional artifact paths or URLs (diff, screenshots, logs)"
          }
        },
        "required": ["action", "summary", "risk"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "check_approval",
      "description": "Check approval status for a previously requested approval_id.",
      "parameters": {
        "type": "object",
        "properties": {
          "approval_id": {"type": "string"}
        },
        "required": ["approval_id"]
      }
    }
  },

  # ---- sandbox ----
  {
    "type": "function",
    "function": {
      "name": "sandbox_run",
      "description": "Run a shell command inside the isolated runner container in a project workspace.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string", "description": "Workspace folder under WORKSPACE_ROOT, e.g. 'proj-123'"},
          "cmd": {"type": "string", "description": "Shell command to run"},
          "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 600}
        },
        "required": ["workspace", "cmd"]
      }
    }
  },

  # ---- workspace ----
  {
    "type": "function",
    "function": {
      "name": "workspace_list_roots",
      "description": "Return all registered workspace IDs and their root paths. Read-only, no arguments. Use this to discover available workspaces before calling other workspace tools.",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "workspace_list",
      "description": "List files/directories under a path in the workspace.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "path": {"type": "string", "description": "Relative path inside workspace", "default": "."}
        },
        "required": ["workspace"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "workspace_read",
      "description": "Read a text file from workspace (small/medium files).",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "path": {"type": "string", "description": "Relative path inside workspace"},
          "max_bytes": {"type": "integer", "default": 200000}
        },
        "required": ["workspace", "path"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "workspace_write",
      "description": "Write or overwrite a text file in workspace.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "path": {"type": "string"},
          "content": {"type": "string"},
          "mkdirs": {"type": "boolean", "default": True}
        },
        "required": ["workspace", "path", "content"]
      }
    }
  },

  # ---- repo (GitHub) ----
  {
    "type": "function",
    "function": {
      "name": "repo_commit",
      "description": "Commit changes in the workspace git repo (local commit).",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "message": {"type": "string"},
          "add_all": {"type": "boolean", "default": True}
        },
        "required": ["workspace", "message"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "repo_open_pr",
      "description": "Push branch to GitHub and open a PR. Requires repo already configured with remote origin.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "title": {"type": "string"},
          "body": {"type": "string"},
          "head_branch": {"type": "string", "description": "Branch name in workspace"},
          "base_branch": {"type": "string", "default": "main"}
        },
        "required": ["workspace", "title", "body", "head_branch"]
      }
    }
  },

  # ---- browser (Playwright) ----
  {
    "type": "function",
    "function": {
      "name": "browser_goto",
      "description": "Navigate browser to URL in a persistent session.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "session": {"type": "string", "description": "Session name, e.g. 'default'", "default": "default"},
          "url": {"type": "string"}
        },
        "required": ["workspace", "url"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "browser_click",
      "description": "Click element by CSS selector.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "session": {"type": "string", "default": "default"},
          "selector": {"type": "string"}
        },
        "required": ["workspace", "selector"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "browser_type",
      "description": "Type into element by CSS selector.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "session": {"type": "string", "default": "default"},
          "selector": {"type": "string"},
          "text": {"type": "string"},
          "clear_first": {"type": "boolean", "default": True}
        },
        "required": ["workspace", "selector", "text"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "browser_screenshot",
      "description": "Take screenshot and save it under workspace artifacts.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "session": {"type": "string", "default": "default"},
          "path": {"type": "string", "description": "Relative path to save screenshot, e.g. 'artifacts/home.png'"}
        },
        "required": ["workspace", "path"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "browser_extract_text",
      "description": "Extract visible text from page or a selector scope.",
      "parameters": {
        "type": "object",
        "properties": {
          "workspace": {"type": "string"},
          "session": {"type": "string", "default": "default"},
          "selector": {"type": "string", "description": "Optional selector to scope extraction"}
        },
        "required": ["workspace"]
      }
    }
  }
]
