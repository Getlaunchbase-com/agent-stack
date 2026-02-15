/**
 * /api/artifacts/:id — Secure artifact download route
 *
 * SECURITY:
 * - Verifies artifact exists
 * - Checks user has access to parent run's project
 * - Logs all downloads
 * - Returns signed URL or streams file
 * - Never exposes raw filesystem paths
 *
 * Usage: GET /api/artifacts/:id
 * Auth: Required (cookie or bearer token)
 */

import { Router } from "express";
import { getDb } from "../db";
import { agentArtifacts, agentRuns } from "../../drizzle/schema";
import { eq } from "drizzle-orm";
import { verifyProjectAccess } from "../auth/verifyProjectAccess";
import { createReadStream } from "fs";
import { stat } from "fs/promises";
import path from "path";

const artifactsRouter = Router();

/**
 * GET /api/artifacts/:id
 * Download an artifact (screenshot, log, trace, etc.)
 */
artifactsRouter.get("/:id", async (req, res) => {
  const artifactId = req.params.id;
  const user = (req as any).user; // From auth middleware

  // Auth check
  if (!user) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  try {
    const db = await getDb();
    if (!db) {
      return res.status(503).json({ error: "Database unavailable" });
    }

    // Get artifact
    const [artifact] = await db
      .select()
      .from(agentArtifacts)
      .where(eq(agentArtifacts.id, artifactId))
      .limit(1);

    if (!artifact) {
      return res.status(404).json({ error: "Artifact not found" });
    }

    // Get parent run to check project ownership
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

    // CRITICAL: Run must be attached to a project
    if (!run.projectId) {
      console.error(`[Artifacts] Run ${run.id} has no projectId`);
      return res.status(403).json({ error: "Run not attached to a project" });
    }

    // CRITICAL: Verify user has access to this project
    const hasAccess = await verifyProjectAccess(user.id, run.projectId);
    if (!hasAccess) {
      // Log unauthorized attempt
      console.warn(`[Artifacts] Unauthorized access attempt: user=${user.id} artifact=${artifactId} project=${run.projectId}`);
      return res.status(403).json({ error: "Access denied" });
    }

    // Log successful access (for audit trail)
    console.log(`[Artifacts] Download: user=${user.id} artifact=${artifactId} type=${artifact.type} run=${run.id}`);

    // Determine file location
    // If using S3, generate signed URL and redirect
    // If using local storage, stream the file
    const isS3 = artifact.path.startsWith("s3://");

    if (isS3) {
      // Generate signed URL for S3 (15 minute expiry)
      const signedUrl = await generateS3SignedUrl(artifact.path);
      return res.redirect(signedUrl);
    }

    // Local file streaming
    const artifactsDir = path.resolve(process.env.ARTIFACTS_DIR || "/tmp/artifacts") + path.sep;
    const localPath = path.resolve(artifactsDir, artifact.path);

    // Security: Prevent directory traversal (strict check with trailing separator)
    if (!localPath.startsWith(artifactsDir)) {
      console.error(`[Artifacts] Path traversal attempt: ${localPath}`);
      return res.status(403).json({ error: "Invalid path" });
    }

    // Check file exists
    try {
      const stats = await stat(localPath);
      if (!stats.isFile()) {
        return res.status(404).json({ error: "File not found" });
      }
    } catch (err) {
      return res.status(404).json({ error: "File not found" });
    }

    // Set headers
    res.setHeader("Content-Type", artifact.mimeType || "application/octet-stream");
    res.setHeader("Content-Length", artifact.sizeBytes || 0);
    res.setHeader("Content-Disposition", `inline; filename="${artifact.filename || `artifact-${artifactId}`}"`);
    res.setHeader("Cache-Control", "private, max-age=3600"); // Cache for 1 hour

    // Stream file
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

/**
 * Generate S3 signed URL (if using S3 storage)
 */
async function generateS3SignedUrl(s3Path: string): Promise<string> {
  const { S3Client, GetObjectCommand } = await import("@aws-sdk/client-s3");
  const { getSignedUrl } = await import("@aws-sdk/s3-request-presigner");

  const s3Client = new S3Client({
    region: process.env.AWS_REGION || "us-east-1",
  });

  // Parse s3://bucket/key
  const match = s3Path.match(/^s3:\/\/([^/]+)\/(.+)$/);
  if (!match) {
    throw new Error("Invalid S3 path");
  }

  const [, bucket, key] = match;

  const command = new GetObjectCommand({
    Bucket: bucket,
    Key: key,
  });

  // Generate signed URL valid for 15 minutes
  const signedUrl = await getSignedUrl(s3Client, command, { expiresIn: 900 });
  return signedUrl;
}

export { artifactsRouter };
