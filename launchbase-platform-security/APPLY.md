# Security Hardening Files for launchbase-platform

These files belong in `Getlaunchbase-com/launchbase-platform`, NOT in agent-stack.
They are staged here because this session only has push access to agent-stack.

## How to Apply

### 1. Copy new files into launchbase-platform

```bash
# From the launchbase-platform repo root:
cp server/middleware/rateLimiter.ts     <launchbase-platform>/server/middleware/rateLimiter.ts
cp server/auth/verifyProjectAccess.ts   <launchbase-platform>/server/auth/verifyProjectAccess.ts
cp server/routes/artifactsRouter.ts     <launchbase-platform>/server/routes/artifactsRouter.ts
cp server/routers/admin/operatorOS.ts   <launchbase-platform>/server/routers/admin/operatorOS.ts
```

### 2. Apply patches to existing files

```bash
cd <launchbase-platform>
git apply launchbase-platform-security/server/db/schema.patch
git apply launchbase-platform-security/server/routers/incoming_routers.patch
```

### 3. Run migration

```bash
pnpm db:push
```

### 4. Delete this directory from agent-stack after applying

These files should not live in agent-stack permanently.
