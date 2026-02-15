# Security Hardening Files for launchbase-platform

These files belong in `Getlaunchbase-com/launchbase-platform`, NOT in agent-stack.
They are staged here because this session only has push access to agent-stack.

## How to Apply

### 1. Copy new files into launchbase-platform

```bash
# From the agent-stack repo root:
LP=<path-to-launchbase-platform>

mkdir -p "$LP/server/middleware"
mkdir -p "$LP/server/auth"
mkdir -p "$LP/server/routes"
mkdir -p "$LP/server/routers/admin"

cp launchbase-platform-security/server/middleware/rateLimiter.ts     "$LP/server/middleware/rateLimiter.ts"
cp launchbase-platform-security/server/auth/verifyProjectAccess.ts   "$LP/server/auth/verifyProjectAccess.ts"
cp launchbase-platform-security/server/routes/artifactsRouter.ts     "$LP/server/routes/artifactsRouter.ts"
cp launchbase-platform-security/server/routers/admin/operatorOS.ts   "$LP/server/routers/admin/operatorOS.ts"
```

### 2. Apply patches to existing files

```bash
cd "$LP"
git apply <path-to-agent-stack>/launchbase-platform-security/server/db/schema.patch
git apply <path-to-agent-stack>/launchbase-platform-security/server/routers/incoming_routers.patch
```

**What the patches do:**
- `schema.patch`: Adds `projectId` column + index to `agentRuns`, adds `agentArtifacts`, `projects`, and `projectCollaborators` tables
- `incoming_routers.patch`: Imports and mounts `operatorOSRouter` in the tRPC app router

### 3. Wire artifact router into Express app

```typescript
// In server/_core/index.ts (or wherever Express app is configured):
import { artifactsRouter } from "../routes/artifactsRouter";
import { downloadRateLimit } from "../middleware/rateLimiter";

app.use("/api/artifacts", downloadRateLimit, artifactsRouter);
```

### 4. Run migration

```bash
pnpm db:push
```

### 5. Install optional dependencies

```bash
# Redis-backed rate limiting (optional, falls back to in-memory):
npm install ioredis

# S3 artifact storage (if using S3):
npm install @aws-sdk/client-s3 @aws-sdk/s3-request-presigner
```

### 6. Delete this directory from agent-stack after applying

These files should not live in agent-stack permanently.
