# Security Hardening — Deployment Checklist

---

## What Changed

| File | Location | Purpose |
|------|----------|---------|
| `server/db/schema.ts` | Modified | Added `projectId` to `agentRuns`, new `agentArtifacts`, `projects`, `projectCollaborators` tables |
| `server/middleware/rateLimiter.ts` | **New** | Fixed-window rate limiting with Redis + in-memory fallback |
| `server/auth/verifyProjectAccess.ts` | **New** | Project-level access control (admin > owner > collaborator > deny) |
| `server/routes/artifactsRouter.ts` | **New** | Secure artifact download with auth, access check, path traversal protection |
| `server/routers/admin/operatorOS.ts` | **New** | Operator OS router — knowledge/plan/matrix + hardened live agent view |
| `server/routers/_incoming_routers.ts` | Modified | Wired `operatorOSRouter` into `admin` section |

---

## Import Paths (follows existing conventions)

From `server/routers/admin/`:
- tRPC: `../_core/trpc`
- getDb: `../db`
- Schema: `../../drizzle/schema`

From `server/auth/` and `server/routes/`:
- getDb: `../db`
- Schema: `../drizzle/schema`

---

## Architecture

```
UI
 |
launchbase-platform (auth, DB, governance, policy)
 |  - operatorOS router (knowledge, plan, matrix, runs)
 |  - verifyProjectAccess (access control)
 |  - rateLimiter (rate limiting)
 |  - artifactsRouter (secure downloads)
 |
agent-stack (stateless tool execution engine)
 |  - tool router (FastAPI)
 |  - sandbox runner (Docker)
 |  - browser container (Playwright)
```

agent-stack remains stateless. All governance lives in launchbase-platform.

---

## DB Migration Required

Run after merging:

```bash
pnpm db:push
```

This will create/modify:
- `agent_runs.projectId` column + index
- `agent_artifacts` table
- `projects` table
- `project_collaborators` table

---

## Express Wiring Required

In `server/_core/index.ts` (the Express entry point):

```typescript
import { artifactsRouter } from "../routes/artifactsRouter";
import { downloadRateLimit } from "../middleware/rateLimiter";

// After auth middleware, before tRPC handler:
app.use("/api/artifacts", downloadRateLimit, artifactsRouter);
```

---

## Optional: Redis for Distributed Rate Limiting

```bash
npm install ioredis
npm install -D @types/ioredis
```

Set `REDIS_URL=redis://localhost:6379` in `.env`. Without it, in-memory fallback is used (single instance, resets on restart).

---

## Environment Variables

```bash
ARTIFACTS_DIR=/var/www/artifacts       # Local artifact storage
REDIS_URL=redis://localhost:6379       # Optional
AWS_REGION=us-east-1                   # If using S3
AWS_S3_ARTIFACTS_BUCKET=launchbase-artifacts  # If using S3
```

---

## Security Verification Tests

**1. Unauthenticated access**
```bash
curl http://localhost:3000/api/artifacts/any-id
# Expected: 401
```

**2. Cross-project access**
```bash
curl -H "Cookie: session=user-b-session" http://localhost:3000/api/artifacts/user-a-artifact-id
# Expected: 403
```

**3. Directory traversal**
```bash
curl http://localhost:3000/api/artifacts/../../../etc/passwd
# Expected: 403
```

**4. Rate limiting**
```bash
for i in {1..100}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    "http://localhost:3000/trpc/admin.operatorOS.runs.getLiveState?input=%7B%22runId%22:1%7D"
done
# Expected: 200 for first 90, then 429
```

**5. Null projectId guard**
```bash
# Run with null projectId → artifact download should return 403
```
