# Approval Policy Matrix

**Purpose**: Define explicit risk tiers with clear auto/approval rules to prevent "oops" moments while enabling safe autonomy.

## Risk Tier Table

| Tier | Policy | Actions | Examples |
|------|--------|---------|----------|
| **Tier 0** | ✅ Auto | Read-only ops, local tests, file writes inside repo, create PRs | `workspace_read`, `workspace_write`, `sandbox_run` (tests), `repo_commit`, `workspace_list` |
| **Tier 1** | ✅ Auto + Logged | Install deps, run Playwright on public pages, open issues/PR comments | `sandbox_run` (npm install), `browser_goto` (public URLs), GitHub issue/comment API |
| **Tier 2** | ⚠️ Approval | Any auth login, web form submissions, external integration writes (Notion/Slack), staging deploy | `browser_goto` (login pages), `browser_type` (credentials), Notion API writes, staging deploy scripts |
| **Tier 3** | 🛑 Approval + 2-step confirm | Prod deploy, DNS/Namecheap, payments, secrets rotation, merges to main | Production deploy, Namecheap API, Stripe charges, GitHub merge API, secrets manager writes |

## Enforcement Rules

### Tier 0 (Auto)

**Policy**: Execute immediately without human intervention

**Requirements**:
- Log: request_id, tool_name, args_hash, result_summary
- No blocking or approval needed
- Rate limit: 1000 calls/hour per tool

**Applicable Tools**:
- `workspace_read`
- `workspace_write`
- `workspace_list`
- `sandbox_run` (when cmd matches: test, build, lint, format, check)
- `repo_commit`

**Implementation**:
```python
if tier == 0:
    result = execute_tool(tool_name, arguments)
    log_audit_trail(request_id, tool_name, args_hash, result_summary)
    return result
```

### Tier 1 (Auto + Logged)

**Policy**: Execute immediately but log full request + response for audit

**Requirements**:
- Log: full request + response
- Alert user after execution (async notification)
- Rate limit: 100 calls/hour per tool

**Applicable Tools**:
- `sandbox_run` (when cmd matches: install, npm, pip, apt-get, yarn, pnpm)
- `browser_goto` (when URL in BROWSER_ALLOWED_DOMAINS)
- `browser_screenshot`
- `browser_extract_text`
- `repo_open_pr`

**Implementation**:
```python
if tier == 1:
    log_full_request(request_id, tool_name, arguments)
    result = execute_tool(tool_name, arguments)
    log_full_response(request_id, result)
    send_async_notification(user_id, f"Tool {tool_name} executed")
    return result
```

### Tier 2 (Approval)

**Policy**: Block execution until human approves

**Requirements**:
- Call `request_approval(action, summary, risk="medium", artifacts)`
- Wait for human approval (poll `check_approval`)
- Timeout: 24 hours, then auto-deny
- Log: full audit trail with approval decision

**Applicable Tools**:
- `browser_click` (any page)
- `browser_type` (any page)
- `browser_goto` (when URL requires auth or not in allowlist)
- External API writes (Notion, Slack, Launchbase)
- Staging deploy scripts

**Implementation**:
```python
if tier == 2:
    approval_id = request_approval(
        action=tool_name,
        summary=f"Execute {tool_name} with args: {args_summary}",
        risk="medium",
        artifacts=[diff_url, screenshot_url]
    )
    
    # Poll for approval (orchestrator handles this)
    while True:
        status = check_approval(approval_id)
        if status["approval"]["status"] == "approved":
            break
        elif status["approval"]["status"] == "denied":
            return {"ok": False, "error": "Approval denied by human"}
        elif time_elapsed > 24_hours:
            return {"ok": False, "error": "Approval timeout"}
        time.sleep(60)
    
    result = execute_tool(tool_name, arguments)
    log_audit_trail(request_id, tool_name, args_hash, result_summary, approval_id)
    return result
```

### Tier 3 (Approval + 2-step confirm)

**Policy**: Block execution until human approves with explicit 2-step confirmation

**Requirements**:
- Call `request_approval(action, summary, risk="high", artifacts)`
- Require explicit 2-step confirmation (e.g., "type 'CONFIRM' to proceed")
- Timeout: 1 hour, then auto-deny
- Log: full audit trail + screenshot/diff artifacts
- Send SMS/email alert (optional)

**Applicable Tools**:
- GitHub merge API
- Production deploy scripts
- Namecheap API
- Stripe API
- Secrets manager writes
- Database migrations (production)

**Implementation**:
```python
if tier == 3:
    approval_id = request_approval(
        action=tool_name,
        summary=f"⚠️ HIGH RISK: Execute {tool_name} with args: {args_summary}",
        risk="high",
        artifacts=[diff_url, screenshot_url, impact_analysis_url]
    )
    
    send_alert(user_id, f"HIGH RISK approval required: {tool_name}")
    
    # Poll for approval with 2-step confirm
    while True:
        status = check_approval(approval_id)
        if status["approval"]["status"] == "approved" and status["approval"]["confirmed"]:
            break
        elif status["approval"]["status"] == "denied":
            return {"ok": False, "error": "Approval denied by human"}
        elif time_elapsed > 1_hour:
            return {"ok": False, "error": "Approval timeout (1 hour)"}
        time.sleep(30)
    
    result = execute_tool(tool_name, arguments)
    log_audit_trail(request_id, tool_name, args_hash, result_summary, approval_id)
    send_alert(user_id, f"HIGH RISK action completed: {tool_name}")
    return result
```

## Tool-to-Tier Mapping

### Tier 0 Tools
- `workspace_read`
- `workspace_write`
- `workspace_list`
- `sandbox_run` (safe commands: test, build, lint, format, check, diff)
- `repo_commit`

### Tier 1 Tools
- `sandbox_run` (install commands: npm, pip, apt-get, yarn, pnpm, cargo)
- `browser_goto` (public URLs in allowlist)
- `browser_screenshot`
- `browser_extract_text`
- `repo_open_pr`

### Tier 2 Tools
- `browser_click`
- `browser_type`
- `browser_goto` (auth-required URLs or not in allowlist)
- External API writes (Notion, Slack, Launchbase)
- Staging deploy

### Tier 3 Tools
- GitHub merge to main/master
- Production deploy
- Namecheap API (DNS, domain purchase)
- Stripe API (charges, subscriptions)
- Secrets manager writes
- Database migrations (production)

## Dynamic Tier Assignment

Some tools have dynamic tier assignment based on arguments:

### `sandbox_run`
- **Tier 0** if cmd matches: `test`, `build`, `lint`, `format`, `check`, `diff`
- **Tier 1** if cmd matches: `install`, `npm`, `pip`, `apt-get`, `yarn`, `pnpm`
- **Tier 2** if cmd matches: `deploy`, `publish`, `release` (staging)
- **Tier 3** if cmd matches: `deploy --prod`, `publish --prod`, `release --prod`

### `browser_goto`
- **Tier 1** if URL in `BROWSER_ALLOWED_DOMAINS` and no auth required
- **Tier 2** if URL requires authentication or not in allowlist

### GitHub operations
- **Tier 1**: `repo_open_pr` (create PR)
- **Tier 2**: PR comments, issue creation
- **Tier 3**: Merge to default branch

## Audit Trail Requirements

Every tool call MUST be logged with:

```json
{
  "request_id": "uuid",
  "user_id": "string",
  "tool_name": "string",
  "args_hash": "sha256",
  "result_summary": "string",
  "timestamp": "iso8601",
  "duration_ms": "integer",
  "risk_tier": "0|1|2|3",
  "approval_id": "uuid|null",
  "approval_status": "auto|approved|denied|timeout|null"
}
```

**Retention**: 90 days minimum

**Export**: CSV/JSON download for compliance

## Rate Limits

| Tier | Per-Tool Limit | Global Limit |
|------|----------------|--------------|
| Tier 0 | 1000/hour | 500/hour |
| Tier 1 | 100/hour | 500/hour |
| Tier 2 | 10/hour | 500/hour |
| Tier 3 | 10/hour | 500/hour |

## Secret Redaction

All logs MUST redact:

- Environment variables matching: `*_TOKEN`, `*_KEY`, `*_SECRET`, `*_PASSWORD`
- Tool arguments matching: `password`, `token`, `api_key`, `secret`, `credentials`
- Command output patterns: `ghp_*`, `sk_*`, `Bearer *`, `Authorization: *`
- File contents: `.env`, `secrets.json`, `credentials.yml`

## Approval UI Requirements

### Tier 2 Approval Screen
- Action name
- Summary (1 paragraph)
- Risk level badge (⚠️ MEDIUM)
- Artifacts (diffs, screenshots, logs)
- Approve / Deny buttons
- Timeout countdown (24 hours)

### Tier 3 Approval Screen
- Action name
- Summary (1 paragraph)
- Risk level badge (🛑 HIGH RISK)
- Artifacts (diffs, screenshots, impact analysis)
- 2-step confirmation:
  1. Read impact summary
  2. Type "CONFIRM" to proceed
- Approve / Deny buttons
- Timeout countdown (1 hour)
- SMS/email alert sent

## Future Enhancements

- [ ] Machine learning-based tier prediction
- [ ] User-specific tier overrides
- [ ] Approval delegation (manager approval)
- [ ] Batch approval for similar actions
- [ ] Rollback mechanism for approved actions
- [ ] Approval analytics dashboard
