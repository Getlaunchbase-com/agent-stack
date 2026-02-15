# Production Deployment Checklist — Security Hardening

**Date**: 2026-02-15

---

## Files Added

| File | Purpose |
|------|---------|
| `server/middleware/rateLimiter.ts` | Fixed-window rate limiting with Redis + in-memory fallback |
| `server/auth/verifyProjectAccess.ts` | Project-level access control (owner, collaborator, admin) |
| `server/routes/artifactsRouter.ts` | Secure artifact download with auth, access check, path traversal protection |
| `server/routers/admin/operatorOS.ts` | Hardened Operator OS router with live DB queries and access enforcement |

---

## Security Fixes Applied

### 1. Rate Limiting
- `getLiveState` enforces 90 req/min per user via `checkRateLimit()` at procedure level
- Express middleware presets: `downloadRateLimit` (60/min), `pollingRateLimit` (90/min), `standardRateLimit` (120/min), `strictRateLimit` (10/min)
- Redis-backed when `REDIS_URL` set; in-memory fallback otherwise
- Standard `X-RateLimit-*` and `Retry-After` headers on all rate-limited responses

### 2. Project Access Control
- Every `runs.*` procedure verifies `ctx.user` has access to the run's project via `verifyProjectAccess()`
- Access hierarchy: admin > project owner > active collaborator > denied
- Null `projectId` on a run returns 403 (not silently ignored)
- Fails closed: any error in access check = deny

### 3. Directory Traversal Protection
- Artifact downloads resolve paths against `ARTIFACTS_DIR` with trailing `path.sep`
- `path.resolve()` + `startsWith()` check blocks `../` and symlink escapes
- Raw filesystem paths never exposed to clients; artifact IDs mapped to `/api/artifacts/:id`

### 4. Artifact Security
- Auth required (401 if no user)
- Project access required (403 if unauthorized)
- S3 paths generate 15-minute signed URLs
- Local files streamed with proper Content-Type/Content-Disposition headers
- All downloads logged for audit trail

---

## Wiring Instructions

### Mount artifact router in Express app

```typescript
// server/_core/index.ts
import { artifactsRouter } from "../routes/artifactsRouter";
import { downloadRateLimit } from "../middleware/rateLimiter";

app.use("/api/artifacts", downloadRateLimit, artifactsRouter);
```

### Mount operatorOS in tRPC app router

```typescript
// Already exports operatorOSRouter — mount at appRouter.admin.operatorOS
```

---

## Required Environment Variables

```bash
ARTIFACTS_DIR=/var/www/artifacts       # Local artifact storage path
REDIS_URL=redis://localhost:6379       # Optional, uses in-memory if not set
AWS_REGION=us-east-1                   # If using S3
AWS_S3_ARTIFACTS_BUCKET=launchbase-artifacts  # If using S3
```

---

## Required Database Tables

### `projects`
```sql
CREATE TABLE projects (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  createdBy INT NOT NULL,
  status ENUM('active', 'archived') DEFAULT 'active' NOT NULL,
  createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
  updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
  INDEX projects_createdBy_idx (createdBy),
  INDEX projects_status_idx (status)
);
```

### `project_collaborators`
```sql
CREATE TABLE project_collaborators (
  id INT AUTO_INCREMENT PRIMARY KEY,
  projectId VARCHAR(36) NOT NULL,
  userId INT NOT NULL,
  role ENUM('viewer', 'editor', 'admin') DEFAULT 'viewer' NOT NULL,
  status ENUM('active', 'revoked') DEFAULT 'active' NOT NULL,
  createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
  INDEX pc_projectId_idx (projectId),
  INDEX pc_userId_idx (userId),
  UNIQUE INDEX pc_project_user_idx (projectId, userId)
);
```

### `agent_runs.projectId` column
```sql
ALTER TABLE agent_runs ADD COLUMN projectId VARCHAR(36);
CREATE INDEX agent_runs_projectId_idx ON agent_runs(projectId);
```

### `agent_artifacts`
```sql
CREATE TABLE agent_artifacts (
  id VARCHAR(36) PRIMARY KEY,
  runId INT NOT NULL,
  type ENUM('screenshot', 'log', 'trace', 'report', 'file', 'video') NOT NULL,
  path VARCHAR(512) NOT NULL,
  filename VARCHAR(255),
  mimeType VARCHAR(128),
  sizeBytes INT,
  metadata JSON,
  createdAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
  INDEX agent_artifacts_runId_idx (runId),
  INDEX agent_artifacts_type_idx (type),
  INDEX agent_artifacts_createdAt_idx (createdAt)
);
```

---

## Security Verification Tests

### Test 1: Unauthenticated artifact access
```bash
curl http://localhost:3000/api/artifacts/any-id
# Expected: 401 "Unauthorized"
```

### Test 2: Cross-project artifact access
```bash
curl -H "Cookie: session=user-b-session" http://localhost:3000/api/artifacts/user-a-artifact-id
# Expected: 403 "Access denied"
```

### Test 3: Directory traversal
```bash
curl http://localhost:3000/api/artifacts/../../../etc/passwd
curl http://localhost:3000/api/artifacts/..%2F..%2F..%2Fetc%2Fpasswd
# Expected: 403 "Invalid path"
```

### Test 4: Rate limiting
```bash
for i in {1..100}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    "http://localhost:3000/trpc/admin.operatorOS.runs.getLiveState?input=%7B%22runId%22:1%7D"
done
# Expected: 200 for first 90, then 429
```

### Test 5: Null projectId guard
```bash
# Insert run with null projectId, create artifact for it, try download
# Expected: 403 "Run not attached to a project"
```

---

## Dependencies

```bash
npm install ioredis
npm install -D @types/ioredis

# If using S3 for artifacts:
npm install @aws-sdk/client-s3 @aws-sdk/s3-request-presigner
```
