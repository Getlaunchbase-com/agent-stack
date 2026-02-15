/**
 * Rate Limiter Middleware
 *
 * Production-safe rate limiting with Redis support + in-memory fallback.
 *
 * FEATURES:
 * - Fixed window (simple, performant)
 * - Redis-backed if REDIS_URL set
 * - In-memory fallback if Redis unavailable
 * - Per-user rate limiting (falls back to IP for unauthenticated)
 * - Standard rate limit headers (X-RateLimit-Limit, -Remaining, -Reset)
 * - Configurable per endpoint
 *
 * Usage:
 *   import { downloadRateLimit } from "../middleware/rateLimiter";
 *   app.get('/api/artifacts/:id', downloadRateLimit, handler);
 *
 *   // Or direct check in tRPC procedure:
 *   import { checkRateLimit } from "../../middleware/rateLimiter";
 *   const info = await checkRateLimit(key, { max: 60, windowSec: 60 });
 *   if (!info.allowed) throw new TRPCError({ code: "TOO_MANY_REQUESTS" });
 */

import type { Request, Response, NextFunction } from "express";

type LimitOptions = { max: number; windowSec: number };

type Counter = { count: number; resetAtMs: number };
const mem = new Map<string, Counter>();

// Clean up expired entries every 60 seconds
setInterval(() => {
  const now = Date.now();
  for (const [key, value] of mem.entries()) {
    if (value.resetAtMs <= now) mem.delete(key);
  }
}, 60_000);

// Optional Redis (only if REDIS_URL is set and ioredis installed)
let redis: any = null;
let redisAttempted = false;

async function getRedis() {
  if (redis) return redis;
  if (redisAttempted) return null;
  redisAttempted = true;

  if (!process.env.REDIS_URL) {
    console.log("[RateLimiter] REDIS_URL not set, using in-memory fallback");
    return null;
  }

  try {
    const { default: IORedis } = await import("ioredis");
    redis = new IORedis(process.env.REDIS_URL, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        return Math.min(times * 50, 2000);
      },
    });

    redis.on("error", (err: Error) => {
      console.error("[RateLimiter] Redis error:", err.message);
    });

    redis.on("connect", () => {
      console.log("[RateLimiter] Redis connected");
    });

    return redis;
  } catch (error) {
    console.error("[RateLimiter] Failed to load ioredis:", error);
    return null;
  }
}

/**
 * Check rate limit for a key.
 *
 * @param key - Unique identifier (e.g., "rl:downloads:user:123")
 * @param opts - { max, windowSec }
 * @returns Rate limit info
 */
export async function checkRateLimit(
  key: string,
  opts: LimitOptions
): Promise<{
  allowed: boolean;
  current: number;
  limit: number;
  resetAtMs: number;
}> {
  const now = Date.now();
  const windowMs = opts.windowSec * 1000;

  const r = await getRedis();
  if (r) {
    try {
      const tx = r.multi();
      tx.incr(key);
      tx.ttl(key);
      const results = await tx.exec();

      if (!results) throw new Error("Redis transaction failed");

      const count = Number(results[0][1] || 0);
      const ttl = Number(results[1][1] || -1);

      if (ttl === -1) {
        await r.expire(key, opts.windowSec);
      }

      const resetAtMs =
        now + Math.max(0, (ttl > 0 ? ttl : opts.windowSec) * 1000);

      return { allowed: count <= opts.max, current: count, limit: opts.max, resetAtMs };
    } catch (error) {
      console.error("[RateLimiter] Redis error, falling back to memory:", error);
    }
  }

  // In-memory fallback
  const existing = mem.get(key);
  if (!existing || existing.resetAtMs <= now) {
    const resetAtMs = now + windowMs;
    mem.set(key, { count: 1, resetAtMs });
    return { allowed: true, current: 1, limit: opts.max, resetAtMs };
  }

  existing.count += 1;
  return {
    allowed: existing.count <= opts.max,
    current: existing.count,
    limit: opts.max,
    resetAtMs: existing.resetAtMs,
  };
}

/**
 * Generate rate limit key from request.
 */
function getClientKey(req: Request, userId?: string | number): string {
  const ip =
    (req.headers["x-forwarded-for"] as string)?.split(",")[0]?.trim() ||
    req.socket.remoteAddress ||
    "unknown";
  return userId ? `u:${userId}` : `ip:${ip}`;
}

/**
 * Create Express middleware for rate limiting.
 */
export function makeRateLimitMiddleware(opts: LimitOptions, keyPrefix: string) {
  return async (req: Request, res: Response, next: NextFunction) => {
    const user = (req as any).user;
    const key = `${keyPrefix}:${getClientKey(req, user?.id)}`;

    try {
      const info = await checkRateLimit(key, opts);

      res.setHeader("X-RateLimit-Limit", String(info.limit));
      res.setHeader("X-RateLimit-Remaining", String(Math.max(0, info.limit - info.current)));
      res.setHeader("X-RateLimit-Reset", String(Math.floor(info.resetAtMs / 1000)));

      if (!info.allowed) {
        const retryAfter = Math.ceil((info.resetAtMs - Date.now()) / 1000);
        res.setHeader("Retry-After", String(retryAfter));
        console.warn(
          `[RateLimiter] Rate limit exceeded: key=${key} current=${info.current} limit=${info.limit}`
        );
        return res.status(429).json({ error: "Rate limit exceeded. Please slow down.", retryAfter });
      }

      next();
    } catch (error) {
      console.error("[RateLimiter] Error:", error);
      next(); // Fail open
    }
  };
}

// ─── Preset Configurations ────────────────────────────────────────────────────

/** 60 downloads per minute */
export const downloadRateLimit = makeRateLimitMiddleware(
  { max: 60, windowSec: 60 },
  "rl:artifact_download"
);

/** 90 requests per minute (1.5/s, allows burst) */
export const pollingRateLimit = makeRateLimitMiddleware(
  { max: 90, windowSec: 60 },
  "rl:live_poll"
);

/** 120 requests per minute */
export const standardRateLimit = makeRateLimitMiddleware(
  { max: 120, windowSec: 60 },
  "rl:api"
);

/** 10 requests per minute (expensive operations) */
export const strictRateLimit = makeRateLimitMiddleware(
  { max: 10, windowSec: 60 },
  "rl:strict"
);

// ─── Graceful Shutdown ─────────────────────────────────────────────────────────

export async function closeRateLimiter() {
  if (redis) {
    await redis.quit();
    redis = null;
  }
  mem.clear();
}
