/**
 * Operator OS — Knowledge & Control Plane Router
 *
 * Implements three first-class state artifacts:
 *   1. KnowledgeLedger  — what the system KNOWS (append-only facts, rules, constraints)
 *   2. ActivePlan       — what is BEING DONE (single active execution plan + tasks)
 *   3. CapabilityMatrix — what the agent CAN and CANNOT do (confirmed access + missing reqs)
 *
 * + Live Agent View (runs, artifacts, events) with:
 *   - Procedure-level rate limiting on getLiveState
 *   - Project access verification on all run/artifact/event queries
 *   - Artifact paths transformed to controlled routes (no raw FS paths)
 *
 * Storage: JSON blobs in memory keyed by projectId (knowledge/plan/matrix).
 * Runs/artifacts/events: MySQL via Drizzle ORM.
 *
 * Zero breaking changes — mounts at appRouter.admin.operatorOS
 */

import { z } from "zod";
import { router, protectedProcedure } from "../../_core/trpc";
import { TRPCError } from "@trpc/server";
import { getDb } from "../../db";
import { agentRuns, agentEvents, agentArtifacts } from "../../../drizzle/schema";
import { eq, desc, and } from "drizzle-orm";
import { verifyProjectAccess } from "../../auth/verifyProjectAccess";
import { checkRateLimit } from "../../middleware/rateLimiter";

// ─── Zod Schemas ──────────────────────────────────────────────────────────────

const VerifiedFactSchema = z.object({
  id: z.string(),
  fact: z.string(),
  source: z.enum(["operator", "system", "test"]),
  timestamp: z.number(),
  locked: z.boolean().default(false),
});

const KnowledgedLedgerSchema = z.object({
  projectId: z.string(),
  environment: z.object({
    mode: z.enum(["staging", "production"]),
    deployment: z.enum(["custom-vps", "vercel", "render", "unknown"]),
    outboundAllowed: z.boolean(),
  }),
  workspaces: z.object({
    registered: z.array(z.string()),
    lastValidatedAt: z.number().optional(),
  }),
  capabilities: z.object({
    toolsAvailable: z.array(z.string()),
    swarmEnabled: z.boolean(),
    playwrightEnabled: z.boolean(),
  }),
  constraints: z.object({
    noPublish: z.boolean(),
    noOutbound: z.boolean(),
    noSchemaChanges: z.boolean(),
  }),
  knownIssues: z.object({
    blocking: z.array(z.string()),
    nonBlocking: z.array(z.string()),
  }),
  verifiedFacts: z.array(VerifiedFactSchema),
  updatedAt: z.number(),
});

const TaskSchema = z.object({
  id: z.string(),
  description: z.string(),
  status: z.enum(["pending", "running", "blocked", "done"]),
  priority: z.enum(["critical", "high", "medium", "low"]).default("medium"),
  blocker: z.string().optional(),
  linkedRunIds: z.array(z.string()).default([]),
  definitionOfDone: z.string().optional(),
  createdAt: z.number(),
  updatedAt: z.number(),
});

const ActivePlanSchema = z.object({
  projectId: z.string(),
  objective: z.string(),
  phase: z.enum(["discussion", "execution", "validation", "complete"]),
  tasks: z.array(TaskSchema),
  stopConditions: z.array(z.string()),
  lockedAt: z.number().optional(),
  lockedBy: z.string().optional(),
  updatedAt: z.number(),
});

const CapabilityMatrixSchema = z.object({
  projectId: z.string(),
  confirmedAccess: z.object({
    workspaces: z.array(z.string()),
    credentials: z.array(z.string()),
    tools: z.array(z.string()),
  }),
  missingRequirements: z.array(
    z.object({
      id: z.string(),
      requirement: z.string(),
      neededFor: z.string(),
      severity: z.enum(["blocking", "degraded", "optional"]).default("blocking"),
    })
  ),
  lastAuditAt: z.number().optional(),
  updatedAt: z.number(),
});

const ProjectSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().optional(),
  env: z.enum(["staging", "production"]),
  tag: z.string().optional(),
  createdAt: z.number(),
  updatedAt: z.number(),
});

// ─── In-Memory Store (swap for DB in production) ──────────────────────────────

type KnowledgeLedger = z.infer<typeof KnowledgedLedgerSchema>;
type ActivePlan = z.infer<typeof ActivePlanSchema>;
type CapabilityMatrix = z.infer<typeof CapabilityMatrixSchema>;
type Project = z.infer<typeof ProjectSchema>;

const store: {
  projects: Map<string, Project>;
  ledgers: Map<string, KnowledgeLedger>;
  plans: Map<string, ActivePlan>;
  matrices: Map<string, CapabilityMatrix>;
} = {
  projects: new Map(),
  ledgers: new Map(),
  plans: new Map(),
  matrices: new Map(),
};

// Seed a default project on startup
const DEFAULT_PROJECT: Project = {
  id: "launchbase-main",
  name: "LaunchBase Platform",
  description: "Core platform + agent stack",
  env: "staging",
  tag: "core",
  createdAt: Date.now(),
  updatedAt: Date.now(),
};
store.projects.set(DEFAULT_PROJECT.id, DEFAULT_PROJECT);

// Seed default knowledge ledger
const DEFAULT_LEDGER: KnowledgeLedger = {
  projectId: "launchbase-main",
  environment: { mode: "staging", deployment: "custom-vps", outboundAllowed: true },
  workspaces: {
    registered: ["launchbase-main", "agent-stack", "showrooms"],
    lastValidatedAt: Date.now(),
  },
  capabilities: {
    toolsAvailable: [
      "sandbox_run", "workspace_read", "workspace_write", "workspace_list",
      "repo_commit", "repo_open_pr", "browser_goto", "browser_click",
      "browser_type", "browser_screenshot", "request_approval", "check_approval",
    ],
    swarmEnabled: true,
    playwrightEnabled: true,
  },
  constraints: { noPublish: true, noOutbound: false, noSchemaChanges: false },
  knownIssues: {
    blocking: [
      "DB schema mismatch — swarm_runs query failing (pnpm db:push needed)",
      "Admin auth error 10002 — owner locked out of /admin/* routes",
    ],
    nonBlocking: [
      "attempts.jsonl writes to jobId dir instead of runId dir",
      "Fixture library smoke test pending (f1-f10)",
      "Control soak test (24 runs) not yet executed",
    ],
  },
  verifiedFacts: [
    {
      id: "f1", fact: "Agent-stack router healthy at port 8080", source: "system",
      timestamp: Date.now() - 60_000, locked: true,
    },
    {
      id: "f2", fact: "580/580 tests passing — 0 TypeScript errors", source: "system",
      timestamp: Date.now() - 3_600_000, locked: true,
    },
    {
      id: "f3", fact: "Phase 2.3 Gate 4 complete — showroom runner & benchmark runs done",
      source: "operator", timestamp: Date.now() - 86_400_000, locked: false,
    },
    {
      id: "f4", fact: "AIML credits restored — gpt-4o-2024-08-06 as champion model",
      source: "system", timestamp: Date.now() - 172_800_000, locked: false,
    },
  ],
  updatedAt: Date.now(),
};
store.ledgers.set(DEFAULT_LEDGER.projectId, DEFAULT_LEDGER);

// Seed default active plan
const DEFAULT_PLAN: ActivePlan = {
  projectId: "launchbase-main",
  objective: "Wire Orchestrator Brain Loop + Unblock Admin Console",
  phase: "execution",
  tasks: [
    {
      id: "t1", description: "Fix DB schema sync — run pnpm db:push for swarm_runs columns",
      status: "pending", priority: "critical", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
    {
      id: "t2", description: "Fix admin auth: gate dev bypass + login redirect for /admin/*",
      status: "pending", priority: "critical", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
    {
      id: "t3", description: "Build server/agent/orchestrator.ts — aimlapi brain loop with tool schemas",
      status: "pending", priority: "high", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
    {
      id: "t4", description: "Wire approval pause/resume with stateJson + pendingActionJson",
      status: "pending", priority: "high", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
    {
      id: "t5", description: "Define craft.schema.ts + critic.schema.ts + write prompt packs",
      status: "pending", priority: "medium", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
    {
      id: "t6", description: "Run Pilot #1 — Claude 3.5 Sonnet as Critic (Web x 2 + Marketing x 2)",
      status: "pending", priority: "medium", linkedRunIds: [], createdAt: Date.now(), updatedAt: Date.now(),
    },
  ],
  stopConditions: [
    "3 consecutive tool failures -> stop & report",
    "Cost cap exceeded -> pause for approval",
    "Schema mismatch detected -> halt, no writes",
    "Unrecognized tool error -> escalate immediately",
  ],
  updatedAt: Date.now(),
};
store.plans.set(DEFAULT_PLAN.projectId, DEFAULT_PLAN);

// Seed default capability matrix
const DEFAULT_MATRIX: CapabilityMatrix = {
  projectId: "launchbase-main",
  confirmedAccess: {
    workspaces: [
      "launchbase-main (/home/info/agent-stack/default)",
      "agent-stack (docker volume)",
    ],
    credentials: [
      "GITHUB_TOKEN (repo scope — verified)",
      "AIML_API_KEY (function calling — verified)",
      "STRIPE_KEY (test mode — verified)",
    ],
    tools: [
      "sandbox_run", "workspace_read", "workspace_write", "workspace_list",
      "repo_commit", "repo_open_pr",
      "browser_goto", "browser_click", "browser_type", "browser_screenshot",
    ],
  },
  missingRequirements: [
    {
      id: "r1",
      requirement: "DB connection from orchestrator service",
      neededFor: "Agent run persistence (agent_runs table writes)",
      severity: "blocking",
    },
    {
      id: "r2",
      requirement: "ROUTER_AUTH_TOKEN in LaunchBase env",
      neededFor: "LaunchBase backend -> agent-stack HTTP calls",
      severity: "blocking",
    },
    {
      id: "r3",
      requirement: "ADMIN_EMAILS env var set",
      neededFor: "Admin auth allowlist enforcement (/admin/* routes)",
      severity: "blocking",
    },
  ],
  lastAuditAt: Date.now() - 300_000,
  updatedAt: Date.now(),
};
store.matrices.set(DEFAULT_MATRIX.projectId, DEFAULT_MATRIX);

// ─── Router ───────────────────────────────────────────────────────────────────

export const operatorOSRouter = router({

  // ── Projects ──────────────────────────────────────────────────────────────

  projects: router({
    list: protectedProcedure.query(() => {
      return Array.from(store.projects.values()).sort((a, b) => b.updatedAt - a.updatedAt);
    }),

    get: protectedProcedure
      .input(z.object({ id: z.string() }))
      .query(({ input }) => {
        return store.projects.get(input.id) ?? null;
      }),

    create: protectedProcedure
      .input(z.object({
        name: z.string().min(1).max(100),
        description: z.string().optional(),
        env: z.enum(["staging", "production"]).default("staging"),
        tag: z.string().optional(),
      }))
      .mutation(({ input }) => {
        const id = `project-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        const project: Project = {
          id,
          name: input.name,
          description: input.description,
          env: input.env,
          tag: input.tag,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        store.projects.set(id, project);
        return project;
      }),

    update: protectedProcedure
      .input(z.object({
        id: z.string(),
        name: z.string().optional(),
        description: z.string().optional(),
        env: z.enum(["staging", "production"]).optional(),
        tag: z.string().optional(),
      }))
      .mutation(({ input }) => {
        const existing = store.projects.get(input.id);
        if (!existing) throw new Error("Project not found");
        const updated = { ...existing, ...input, updatedAt: Date.now() };
        store.projects.set(input.id, updated);
        return updated;
      }),

    delete: protectedProcedure
      .input(z.object({ id: z.string() }))
      .mutation(({ input }) => {
        store.projects.delete(input.id);
        store.ledgers.delete(input.id);
        store.plans.delete(input.id);
        store.matrices.delete(input.id);
        return { success: true };
      }),
  }),

  // ── Knowledge Ledger ──────────────────────────────────────────────────────

  ledger: router({
    get: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .query(({ input }) => {
        return store.ledgers.get(input.projectId) ?? null;
      }),

    upsert: protectedProcedure
      .input(KnowledgedLedgerSchema.omit({ updatedAt: true }))
      .mutation(({ input }) => {
        const ledger = { ...input, updatedAt: Date.now() };
        store.ledgers.set(input.projectId, ledger);
        return ledger;
      }),

    addFact: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        fact: z.string().min(1),
        source: z.enum(["operator", "system", "test"]),
        locked: z.boolean().default(false),
      }))
      .mutation(({ input }) => {
        const ledger = store.ledgers.get(input.projectId);
        if (!ledger) throw new Error("Ledger not found for project");
        const newFact = {
          id: `fact-${Date.now()}`,
          fact: input.fact,
          source: input.source,
          timestamp: Date.now(),
          locked: input.locked,
        };
        ledger.verifiedFacts.push(newFact);
        ledger.updatedAt = Date.now();
        store.ledgers.set(input.projectId, ledger);
        return newFact;
      }),

    removeFact: protectedProcedure
      .input(z.object({ projectId: z.string(), factId: z.string() }))
      .mutation(({ input }) => {
        const ledger = store.ledgers.get(input.projectId);
        if (!ledger) throw new Error("Ledger not found");
        const fact = ledger.verifiedFacts.find(f => f.id === input.factId);
        if (fact?.locked) throw new Error("Cannot remove a locked fact");
        ledger.verifiedFacts = ledger.verifiedFacts.filter(f => f.id !== input.factId);
        ledger.updatedAt = Date.now();
        store.ledgers.set(input.projectId, ledger);
        return { success: true };
      }),

    addKnownIssue: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        issue: z.string().min(1),
        type: z.enum(["blocking", "nonBlocking"]),
      }))
      .mutation(({ input }) => {
        const ledger = store.ledgers.get(input.projectId);
        if (!ledger) throw new Error("Ledger not found");
        if (input.type === "blocking") {
          ledger.knownIssues.blocking.push(input.issue);
        } else {
          ledger.knownIssues.nonBlocking.push(input.issue);
        }
        ledger.updatedAt = Date.now();
        store.ledgers.set(input.projectId, ledger);
        return { success: true };
      }),

    resolveIssue: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        issue: z.string(),
        type: z.enum(["blocking", "nonBlocking"]),
      }))
      .mutation(({ input }) => {
        const ledger = store.ledgers.get(input.projectId);
        if (!ledger) throw new Error("Ledger not found");
        if (input.type === "blocking") {
          ledger.knownIssues.blocking = ledger.knownIssues.blocking.filter(i => i !== input.issue);
        } else {
          ledger.knownIssues.nonBlocking = ledger.knownIssues.nonBlocking.filter(i => i !== input.issue);
        }
        ledger.updatedAt = Date.now();
        store.ledgers.set(input.projectId, ledger);
        return { success: true };
      }),
  }),

  // ── Active Plan ───────────────────────────────────────────────────────────

  plan: router({
    get: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .query(({ input }) => {
        return store.plans.get(input.projectId) ?? null;
      }),

    replace: protectedProcedure
      .input(ActivePlanSchema.omit({ updatedAt: true }))
      .mutation(({ input }) => {
        const existing = store.plans.get(input.projectId);
        const plan: ActivePlan = {
          ...input,
          tasks: input.tasks.map(t => ({
            ...t,
            createdAt: t.createdAt ?? Date.now(),
            updatedAt: Date.now(),
          })),
          lockedAt: existing?.lockedAt,
          lockedBy: existing?.lockedBy,
          updatedAt: Date.now(),
        };
        store.plans.set(input.projectId, plan);
        return plan;
      }),

    updateTaskStatus: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        taskId: z.string(),
        status: z.enum(["pending", "running", "blocked", "done"]),
        blocker: z.string().optional(),
      }))
      .mutation(({ input }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        const task = plan.tasks.find(t => t.id === input.taskId);
        if (!task) throw new Error("Task not found");
        task.status = input.status;
        task.blocker = input.blocker;
        task.updatedAt = Date.now();
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return task;
      }),

    addTask: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        description: z.string().min(1),
        priority: z.enum(["critical", "high", "medium", "low"]).default("medium"),
        definitionOfDone: z.string().optional(),
      }))
      .mutation(({ input }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        const task = {
          id: `task-${Date.now()}`,
          description: input.description,
          status: "pending" as const,
          priority: input.priority,
          linkedRunIds: [] as string[],
          definitionOfDone: input.definitionOfDone,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        plan.tasks.push(task);
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return task;
      }),

    removeTask: protectedProcedure
      .input(z.object({ projectId: z.string(), taskId: z.string() }))
      .mutation(({ input }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        plan.tasks = plan.tasks.filter(t => t.id !== input.taskId);
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return { success: true };
      }),

    lock: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .mutation(({ input, ctx }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        plan.lockedAt = Date.now();
        plan.lockedBy = (ctx as any).user?.email ?? "operator";
        plan.phase = "execution";
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return plan;
      }),

    unlock: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .mutation(({ input }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        plan.lockedAt = undefined;
        plan.lockedBy = undefined;
        plan.phase = "discussion";
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return plan;
      }),

    transitionPhase: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        phase: z.enum(["discussion", "execution", "validation", "complete"]),
      }))
      .mutation(({ input }) => {
        const plan = store.plans.get(input.projectId);
        if (!plan) throw new Error("Active plan not found");
        plan.phase = input.phase;
        plan.updatedAt = Date.now();
        store.plans.set(input.projectId, plan);
        return plan;
      }),
  }),

  // ── Capability Matrix ─────────────────────────────────────────────────────

  matrix: router({
    get: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .query(({ input }) => {
        return store.matrices.get(input.projectId) ?? null;
      }),

    upsert: protectedProcedure
      .input(CapabilityMatrixSchema.omit({ updatedAt: true }))
      .mutation(({ input }) => {
        const matrix = { ...input, updatedAt: Date.now() };
        store.matrices.set(input.projectId, matrix);
        return matrix;
      }),

    resolveRequirement: protectedProcedure
      .input(z.object({ projectId: z.string(), requirementId: z.string() }))
      .mutation(({ input }) => {
        const matrix = store.matrices.get(input.projectId);
        if (!matrix) throw new Error("Matrix not found");
        matrix.missingRequirements = matrix.missingRequirements.filter(
          r => r.id !== input.requirementId
        );
        matrix.updatedAt = Date.now();
        store.matrices.set(input.projectId, matrix);
        return { success: true };
      }),

    addRequirement: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        requirement: z.string().min(1),
        neededFor: z.string().min(1),
        severity: z.enum(["blocking", "degraded", "optional"]).default("blocking"),
      }))
      .mutation(({ input }) => {
        const matrix = store.matrices.get(input.projectId);
        if (!matrix) throw new Error("Matrix not found");
        const req = {
          id: `req-${Date.now()}`,
          requirement: input.requirement,
          neededFor: input.neededFor,
          severity: input.severity,
        };
        matrix.missingRequirements.push(req);
        matrix.updatedAt = Date.now();
        store.matrices.set(input.projectId, matrix);
        return req;
      }),

    auditNow: protectedProcedure
      .input(z.object({ projectId: z.string() }))
      .mutation(({ input }) => {
        const matrix = store.matrices.get(input.projectId);
        if (!matrix) throw new Error("Matrix not found");
        matrix.lastAuditAt = Date.now();
        matrix.updatedAt = Date.now();
        store.matrices.set(input.projectId, matrix);
        return matrix;
      }),
  }),

  // ── Situation Board ────────────────────────────────────────────────────────

  situationBoard: protectedProcedure
    .input(z.object({ projectId: z.string() }))
    .query(({ input }) => {
      const ledger = store.ledgers.get(input.projectId) ?? null;
      const plan = store.plans.get(input.projectId) ?? null;
      const matrix = store.matrices.get(input.projectId) ?? null;
      const project = store.projects.get(input.projectId) ?? null;

      const blockingIssueCount = ledger?.knownIssues.blocking.length ?? 0;
      const missingReqCount = matrix?.missingRequirements.filter(r => r.severity === "blocking").length ?? 0;
      const pendingTaskCount = plan?.tasks.filter(t => t.status === "pending").length ?? 0;
      const criticalTaskCount = plan?.tasks.filter(t => t.priority === "critical" && t.status === "pending").length ?? 0;

      const readyToExecute =
        blockingIssueCount === 0 &&
        missingReqCount === 0 &&
        plan !== null &&
        plan.phase !== "discussion";

      return {
        project,
        ledger,
        plan,
        matrix,
        summary: {
          blockingIssueCount,
          missingReqCount,
          pendingTaskCount,
          criticalTaskCount,
          readyToExecute,
          canProceed: missingReqCount === 0,
        },
      };
    }),

  // ── Runs (Live Agent View) ────────────────────────────────────────────────

  runs: router({
    list: protectedProcedure
      .input(z.object({
        projectId: z.string(),
        limit: z.number().int().min(1).max(100).default(20),
        status: z.enum(["running", "success", "failed", "awaiting_approval"]).optional(),
      }))
      .query(async ({ input, ctx }) => {
        const db = await getDb();
        if (!db) {
          throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "Database unavailable" });
        }

        // Verify caller has access to this project
        const hasAccess = await verifyProjectAccess(ctx.user.id, input.projectId);
        if (!hasAccess) {
          throw new TRPCError({ code: "FORBIDDEN", message: "Access denied" });
        }

        const conditions = [eq(agentRuns.projectId, input.projectId)];
        if (input.status) {
          conditions.push(eq(agentRuns.status, input.status));
        }

        const runs = await db.select({
          id: agentRuns.id,
          projectId: agentRuns.projectId,
          goal: agentRuns.goal,
          status: agentRuns.status,
          model: agentRuns.model,
          createdAt: agentRuns.createdAt,
          finishedAt: agentRuns.finishedAt,
          workspaceName: agentRuns.workspaceName,
        })
        .from(agentRuns)
        .where(and(...conditions))
        .orderBy(desc(agentRuns.createdAt))
        .limit(input.limit);

        return runs.map(r => ({
          ...r,
          createdAt: r.createdAt.getTime(),
          finishedAt: r.finishedAt?.getTime() ?? null,
        }));
      }),

    getLiveState: protectedProcedure
      .input(z.object({ runId: z.number() }))
      .query(async ({ input, ctx }) => {
        // Rate limiting: prevent polling from melting the DB
        const rateLimitKey = `rl:live_poll:u:${ctx.user.id}`;
        const rateLimitInfo = await checkRateLimit(rateLimitKey, { max: 90, windowSec: 60 });

        if (!rateLimitInfo.allowed) {
          throw new TRPCError({
            code: "TOO_MANY_REQUESTS",
            message: `Rate limit exceeded. Retry after ${Math.ceil((rateLimitInfo.resetAtMs - Date.now()) / 1000)}s`,
          });
        }

        const db = await getDb();
        if (!db) {
          throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "Database unavailable" });
        }

        // Get the run
        const [run] = await db.select().from(agentRuns)
          .where(eq(agentRuns.id, input.runId))
          .limit(1);

        if (!run) {
          throw new TRPCError({ code: "NOT_FOUND", message: "Run not found" });
        }

        // Run must be attached to a project
        if (!run.projectId) {
          console.error(`[operatorOS] Run ${run.id} has no projectId`);
          throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Run not attached to a project" });
        }

        // Verify project ownership
        const hasAccess = await verifyProjectAccess(ctx.user.id, run.projectId);
        if (!hasAccess) {
          throw new TRPCError({ code: "FORBIDDEN", message: "Access denied" });
        }

        // Get latest events for state derivation
        const events = await db.select().from(agentEvents)
          .where(eq(agentEvents.runId, input.runId))
          .orderBy(desc(agentEvents.ts))
          .limit(10);

        // Derive current state from stateJson (orchestrator writes this)
        const state = run.stateJson as any;
        const currentStep = state?.stepCount ?? 0;
        const maxSteps = state?.maxSteps ?? 10;

        // Find most recent tool_call event
        const lastToolCall = events.find(e => e.type === "tool_call");
        const toolPayload = lastToolCall?.payload as any;

        // Compute budget remaining
        const budgetRemaining = Math.max(0, 1 - (currentStep / maxSteps));

        // Last activity = most recent event timestamp
        const lastActivityAt = events[0]?.ts.getTime() ?? Date.now();

        return {
          runId: input.runId,
          status: run.status,
          currentStep,
          maxSteps,
          currentTool: toolPayload?.tool ?? null,
          currentToolArgs: toolPayload?.args ?? null,
          lastError: run.errorMessage,
          budgetRemaining,
          lastActivityAt,
          awaitingApproval: run.status === "awaiting_approval",
        };
      }),

    getArtifacts: protectedProcedure
      .input(z.object({
        runId: z.number(),
        limit: z.number().int().min(1).max(50).default(20),
        type: z.enum(["screenshot", "log", "trace", "report", "file", "video"]).optional(),
      }))
      .query(async ({ input, ctx }) => {
        const db = await getDb();
        if (!db) {
          throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "Database unavailable" });
        }

        // Verify run ownership via projectId before exposing artifacts
        const [run] = await db.select({ projectId: agentRuns.projectId })
          .from(agentRuns)
          .where(eq(agentRuns.id, input.runId))
          .limit(1);

        if (!run) {
          throw new TRPCError({ code: "NOT_FOUND", message: "Run not found" });
        }

        if (!run.projectId) {
          throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Run not attached to a project" });
        }

        const hasAccess = await verifyProjectAccess(ctx.user.id, run.projectId);
        if (!hasAccess) {
          throw new TRPCError({ code: "FORBIDDEN", message: "Access denied" });
        }

        const conditions = [eq(agentArtifacts.runId, input.runId)];
        if (input.type) {
          conditions.push(eq(agentArtifacts.type, input.type));
        }

        const artifacts = await db.select()
          .from(agentArtifacts)
          .where(and(...conditions))
          .orderBy(desc(agentArtifacts.createdAt))
          .limit(input.limit);

        // Transform paths to controlled route — never expose raw filesystem paths
        return artifacts.map(a => ({
          ...a,
          path: `/api/artifacts/${a.id}`,
          createdAt: a.createdAt.getTime(),
        }));
      }),

    getEvents: protectedProcedure
      .input(z.object({
        runId: z.number(),
        limit: z.number().int().min(1).max(100).default(50),
        type: z.enum(["message", "tool_call", "tool_result", "approval_request", "approval_result", "error", "artifact"]).optional(),
      }))
      .query(async ({ input, ctx }) => {
        const db = await getDb();
        if (!db) {
          throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "Database unavailable" });
        }

        // Verify run ownership before exposing events
        const [run] = await db.select({ projectId: agentRuns.projectId })
          .from(agentRuns)
          .where(eq(agentRuns.id, input.runId))
          .limit(1);

        if (!run) {
          throw new TRPCError({ code: "NOT_FOUND", message: "Run not found" });
        }

        if (!run.projectId) {
          throw new TRPCError({ code: "PRECONDITION_FAILED", message: "Run not attached to a project" });
        }

        const hasAccess = await verifyProjectAccess(ctx.user.id, run.projectId);
        if (!hasAccess) {
          throw new TRPCError({ code: "FORBIDDEN", message: "Access denied" });
        }

        const conditions = [eq(agentEvents.runId, input.runId)];
        if (input.type) {
          conditions.push(eq(agentEvents.type, input.type));
        }

        const events = await db.select()
          .from(agentEvents)
          .where(and(...conditions))
          .orderBy(desc(agentEvents.ts))
          .limit(input.limit);

        return events.map(e => ({
          ...e,
          ts: e.ts.getTime(),
        }));
      }),
  }),
});
