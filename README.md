# Agent Stack

**Manus execution + GPT-5.2 reasoning + Launchbase control**

A production-ready agent tool router with Docker-based execution environment, browser automation, and GitHub integration. Built for safe, approval-gated autonomy.

## Architecture

```
Orchestrator (GPT-5.2 via aimlapi)
    ↓
Tool Router (FastAPI)
    ↓
┌─────────────┬──────────────┬──────────────┐
│   Runner    │   Browser    │   GitHub     │
│  (sandbox)  │ (Playwright) │    (API)     │
└─────────────┴──────────────┴──────────────┘
```

## Features

- **13 Tool Functions**: Sandbox execution, file operations, Git/GitHub, browser automation, approval gates
- **4 Risk Tiers**: Auto (Tier 0/1), Approval (Tier 2), 2-step confirm (Tier 3)
- **Security**: Path sandboxing, domain allowlists, audit trail, secret redaction
- **Persistent Sessions**: Browser contexts, workspace isolation, Docker volumes

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- GitHub token (for PR operations)
- (Optional) AIMLAPI key for orchestrator

### 2. Setup

```bash
# Clone repository
cd agent-stack/

# Configure environment
cp .env.example .env
# Edit .env with your tokens:
#   ROUTER_AUTH_TOKEN=your_secret_token
#   GITHUB_TOKEN=ghp_xxxxx
#   GITHUB_OWNER=your-org
#   GITHUB_REPO=your-repo

# Boot stack
docker compose up --build -d

# Check status
docker ps
```

### 3. Smoke Tests

```bash
# Health check
curl http://localhost:8080/health
# Expected: {"ok":true}

# Tool schemas
curl http://localhost:8080/tools | jq '.tools | length'
# Expected: 13

# Test tool call (requires auth token)
curl -X POST http://localhost:8080/tool \
  -H "X-Router-Token: your_secret_token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {
      "name": "workspace_list",
      "arguments": {"workspace": "demo"}
    }
  }'
```

## Golden Demo Flow

**Scenario**: Create file → Read file → Commit → Open PR

### Step 1: Create workspace

```bash
# Create demo workspace with git repo
docker exec agent-runner bash -c "
  mkdir -p /workspaces/demo &&
  cd /workspaces/demo &&
  git init &&
  git config user.name 'Agent' &&
  git config user.email 'agent@example.com' &&
  git remote add origin https://github.com/YOUR_ORG/YOUR_REPO.git &&
  git checkout -b feature/demo
"
```

### Step 2: Write file via tool

```bash
curl -X POST http://localhost:8080/tool \
  -H "X-Router-Token: your_secret_token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {
      "name": "workspace_write",
      "arguments": {
        "workspace": "demo",
        "path": "hello.txt",
        "content": "Hello from agent tool router!"
      }
    }
  }'
# Expected: {"ok":true,"path":"hello.txt","bytes":31}
```

### Step 3: Read file via tool

```bash
curl -X POST http://localhost:8080/tool \
  -H "X-Router-Token: your_secret_token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {
      "name": "workspace_read",
      "arguments": {
        "workspace": "demo",
        "path": "hello.txt"
      }
    }
  }'
# Expected: {"ok":true,"path":"hello.txt","content":"Hello from agent tool router!"}
```

### Step 4: Commit via tool

```bash
curl -X POST http://localhost:8080/tool \
  -H "X-Router-Token: your_secret_token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {
      "name": "repo_commit",
      "arguments": {
        "workspace": "demo",
        "message": "Add hello.txt via agent"
      }
    }
  }'
# Expected: {"ok":true,"details":{...}}
```

### Step 5: Open PR via tool

```bash
curl -X POST http://localhost:8080/tool \
  -H "X-Router-Token: your_secret_token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {
      "name": "repo_open_pr",
      "arguments": {
        "workspace": "demo",
        "title": "Demo: Add hello.txt",
        "body": "Automated PR from agent tool router\n\n- Created hello.txt\n- Demonstrates end-to-end tool workflow",
        "head_branch": "feature/demo"
      }
    }
  }'
# Expected: {"ok":true,"pr_url":"https://github.com/...","number":123}
```

## Tool Reference

### Approval Tools

- `request_approval(action, summary, risk, artifacts)` - Request human approval for gated actions
- `check_approval(approval_id)` - Poll approval status

### Sandbox Tools

- `sandbox_run(workspace, cmd, timeout_sec)` - Execute shell commands in isolated runner

### Workspace Tools

- `workspace_list(workspace, path)` - List files/directories
- `workspace_read(workspace, path, max_bytes)` - Read file contents
- `workspace_write(workspace, path, content, mkdirs)` - Write/overwrite files

### Repo Tools (GitHub)

- `repo_commit(workspace, message, add_all)` - Commit changes locally
- `repo_open_pr(workspace, title, body, head_branch, base_branch)` - Push branch and create PR

### Browser Tools (Playwright)

- `browser_goto(workspace, session, url)` - Navigate to URL
- `browser_click(workspace, session, selector)` - Click element
- `browser_type(workspace, session, selector, text, clear_first)` - Type into field
- `browser_screenshot(workspace, session, path)` - Capture screenshot
- `browser_extract_text(workspace, session, selector)` - Extract text content

## Risk Tiers & Approval Policy

| Tier | Policy | Actions |
|------|--------|---------|
| **Tier 0** | ✅ Auto | Read-only ops, local tests, file writes, create PRs |
| **Tier 1** | ✅ Auto + Logged | Install deps, Playwright on public pages, PR comments |
| **Tier 2** | ⚠️ Approval | Auth logins, form submissions, external API writes, staging deploy |
| **Tier 3** | 🛑 Approval + 2-step | Prod deploy, DNS changes, payments, secrets rotation, merge to main |

See `APPROVAL_POLICY.md` for detailed enforcement rules.

## Security Features

- **Path Traversal Protection**: Workspace name validation, path normalization
- **Domain Allowlist**: Browser restricted to `BROWSER_ALLOWED_DOMAINS`
- **Secret Redaction**: Automatic scrubbing of tokens/keys from logs
- **Rate Limits**: Per-tool and global rate limiting
- **Audit Trail**: Full logging of tool calls with request_id, args_hash, timestamps

## Non-Goals (MVP)

- ❌ No production deploys
- ❌ No DNS/domain changes
- ❌ No spending money
- ❌ No sending email/SMS
- ❌ No merging PRs to main
- ❌ No logging into authenticated sites

## Troubleshooting

### Containers won't start

```bash
# Check logs
docker compose logs router
docker compose logs runner
docker compose logs browser

# Rebuild
docker compose down
docker compose up --build
```

### Tool calls fail with "Invalid workspace"

Ensure workspace name doesn't contain `/` or `..` (path traversal protection).

### GitHub PR creation fails

Check environment variables:
- `GITHUB_TOKEN` must be valid personal access token with `repo` scope
- `GITHUB_OWNER` and `GITHUB_REPO` must match your repository
- Remote origin must be configured in workspace git repo

### Browser tools timeout

Increase timeout in tool call or check browser container logs:
```bash
docker logs agent-browser
```

## Development

### Adding New Tools

1. Add tool schema to `router/app/tool_schemas.py`
2. Implement tool function in appropriate module
3. Register in `router/app/tools.py` TOOL_MAP
4. Update README with tool documentation

### Running Tests

```bash
# TODO: Add pytest tests
# pytest router/tests/
```

## Next Steps

1. **Connect Orchestrator**: Integrate GPT-5.2 agent loop via aimlapi
2. **Add Approval UI**: Build web interface for Tier 2/3 approvals
3. **Launchbase Integration**: Swap mock API for real Launchbase endpoints
4. **Monitoring**: Add metrics, logging, alerting
5. **Multi-tenancy**: Support multiple users/workspaces

## License

MIT

## Contributing

See `CONTRIBUTING.md` (TODO)

## Support

For issues or questions, open a GitHub issue or contact the team.
