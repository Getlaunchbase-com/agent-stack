/**
 * /api/artifacts/:id — Secure artifact download route
 *
 * SECURITY:
 * - Verifies artifact exists in DB
 * - Checks user has access to parent run's project
 * - Guards against null projectId
 * - Prevents directory traversal (trailing path.sep check)
 * - Logs all downloads for audit trail
 * - Returns S3 signed URL or streams local file
 * - Never exposes raw filesystem paths
 *
 * Mount: app.use("/api/artifacts", downloadRateLimit, artifactsRouter);
 * Auth: Required (cookie or bearer token — expects req.user from auth middleware)
 */

import { Router } from "express";
import { getDb } from "../db";
import { agentArtifacts, agentRuns } from "../drizzle/schema";
import { eq } from "drizzle-orm";
import { verifyProjectAccess } from "../auth/verifyProjectAccess";
import { createReadStream } from "fs";
import { stat } from "fs/promises";
import path from "path";

const artifactsRouter = Router();

artifactsRouter.get("/:id", async (req, res) => {
  const artifactId = req.params.id;
  const user = (req as any).user;

  if (!user) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  try {
    const db = await getDb();
    if (!db) {
      return res.status(503).json({ error: "Database unavailable" });
    }

    // Fetch artifact
    const [artifact] = await db
      .select()
      .from(agentArtifacts)
      .where(eq(agentArtifacts.id, artifactId))
      .limit(1);

    if (!artifact) {
      return res.status(404).json({ error: "Artifact not found" });
    }

    // Fetch parent run for project ownership check
    const [run] = await db
      .select({
        id: agentRuns.id,
        projectId: agentRuns.projectId,
        createdBy: agentRuns.createdBy,
      })
      .from(agentRuns)
      .where(eq(agentRuns.id, artifact.runId))
      .limit(1);

    if (!run) {
      return res.status(404).json({ error: "Run not found" });
    }

    // Guard: run must be attached to a project
    if (!run.projectId) {
      console.error(`[Artifacts] Run ${run.id} has no projectId`);
      return res.status(403).json({ error: "Run not attached to a project" });
    }

    // Verify user has access to this project
    const hasAccess = await verifyProjectAccess(user.id, run.projectId);
    if (!hasAccess) {
      console.warn(
        `[Artifacts] Unauthorized access attempt: user=${user.id} artifact=${artifactId} project=${run.projectId}`
      );
      return res.status(403).json({ error: "Access denied" });
    }

    // Audit log
    console.log(
      `[Artifacts] Download: user=${user.id} artifact=${artifactId} type=${artifact.type} run=${run.id}`
    );

    // S3 path → redirect to signed URL
    if (artifact.path.startsWith("s3://")) {
      const signedUrl = await generateS3SignedUrl(artifact.path);
      return res.redirect(signedUrl);
    }

    // Local file streaming
    const artifactsDir =
      path.resolve(process.env.ARTIFACTS_DIR || "/tmp/artifacts") + path.sep;
    const localPath = path.resolve(artifactsDir, artifact.path);

    // Directory traversal protection (strict: trailing path.sep ensures boundary)
    if (!localPath.startsWith(artifactsDir)) {
      console.error(`[Artifacts] Path traversal attempt: ${localPath}`);
      return res.status(403).json({ error: "Invalid path" });
    }

    // Verify file exists and is a regular file
    try {
      const stats = await stat(localPath);
      if (!stats.isFile()) {
        return res.status(404).json({ error: "File not found" });
      }
    } catch {
      return res.status(404).json({ error: "File not found" });
    }

    res.setHeader("Content-Type", artifact.mimeType || "application/octet-stream");
    res.setHeader("Content-Length", artifact.sizeBytes || 0);
    res.setHeader(
      "Content-Disposition",
      `inline; filename="${artifact.filename || `artifact-${artifactId}`}"`
    );
    res.setHeader("Cache-Control", "private, max-age=3600");

    const fileStream = createReadStream(localPath);
    fileStream.pipe(res);

    fileStream.on("error", (err) => {
      console.error(`[Artifacts] Stream error: ${err.message}`);
      if (!res.headersSent) {
        res.status(500).json({ error: "Failed to read file" });
      }
    });
  } catch (error) {
    console.error(`[Artifacts] Error:`, error);
    return res.status(500).json({ error: "Internal server error" });
  }
});

async function generateS3SignedUrl(s3Path: string): Promise<string> {
  const { S3Client, GetObjectCommand } = await import("@aws-sdk/client-s3");
  const { getSignedUrl } = await import("@aws-sdk/s3-request-presigner");

  const s3Client = new S3Client({
    region: process.env.AWS_REGION || "us-east-1",
  });

  const match = s3Path.match(/^s3:\/\/([^/]+)\/(.+)$/);
  if (!match) throw new Error("Invalid S3 path");

  const [, bucket, key] = match;
  const command = new GetObjectCommand({ Bucket: bucket, Key: key });
  return getSignedUrl(s3Client, command, { expiresIn: 900 });
}

export { artifactsRouter };
