/**
 * verifyProjectAccess — Check if user has access to a project
 *
 * SECURITY MODEL:
 * - Project owner (createdBy) has full access
 * - Project collaborators have read access
 * - Admin role has access to all projects
 * - All other users denied
 * - Fails closed: deny on any error
 *
 * Usage:
 *   const hasAccess = await verifyProjectAccess(userId, projectId);
 *   if (!hasAccess) throw new TRPCError({ code: "FORBIDDEN" });
 */

import { getDb } from "../db";
import { users, projects, projectCollaborators } from "../../drizzle/schema";
import { eq, and } from "drizzle-orm";

/**
 * Verify user has access to project (read-level)
 *
 * @param userId - User ID from ctx.user.id
 * @param projectId - Project ID to check access for
 * @returns true if user has access, false otherwise
 */
export async function verifyProjectAccess(
  userId: number,
  projectId: string
): Promise<boolean> {
  const db = await getDb();
  if (!db) {
    console.error("[verifyProjectAccess] Database unavailable");
    return false;
  }

  try {
    // Get user role
    const [user] = await db
      .select({ role: users.role })
      .from(users)
      .where(eq(users.id, userId))
      .limit(1);

    if (!user) {
      console.warn(`[verifyProjectAccess] User not found: ${userId}`);
      return false;
    }

    // Admins have access to all projects
    if (user.role === "admin") {
      return true;
    }

    // Check project exists and user is owner
    const [project] = await db
      .select({ createdBy: projects.createdBy })
      .from(projects)
      .where(eq(projects.id, projectId))
      .limit(1);

    if (!project) {
      console.warn(`[verifyProjectAccess] Project not found: ${projectId}`);
      return false;
    }

    // User is project owner
    if (project.createdBy === userId) {
      return true;
    }

    // Check collaborators table
    const [collaborator] = await db
      .select({ userId: projectCollaborators.userId })
      .from(projectCollaborators)
      .where(
        and(
          eq(projectCollaborators.projectId, projectId),
          eq(projectCollaborators.userId, userId),
          eq(projectCollaborators.status, "active")
        )
      )
      .limit(1);

    if (collaborator) {
      return true;
    }

    // Deny access — no ownership or collaboration found
    console.warn(
      `[verifyProjectAccess] Access denied: user=${userId} project=${projectId} (no ownership or collaboration)`
    );
    return false;
  } catch (error) {
    console.error(`[verifyProjectAccess] Error:`, error);
    return false; // Fail closed — deny on error
  }
}

/**
 * Verify user owns a project (stricter than read access)
 *
 * @param userId - User ID
 * @param projectId - Project ID
 * @returns true if user is project owner or admin
 */
export async function verifyProjectOwnership(
  userId: number,
  projectId: string
): Promise<boolean> {
  const db = await getDb();
  if (!db) return false;

  try {
    // Get user role
    const [user] = await db
      .select({ role: users.role })
      .from(users)
      .where(eq(users.id, userId))
      .limit(1);

    if (!user) return false;

    // Admins have ownership rights on all projects
    if (user.role === "admin") {
      return true;
    }

    // Check direct ownership
    const [project] = await db
      .select({ createdBy: projects.createdBy })
      .from(projects)
      .where(eq(projects.id, projectId))
      .limit(1);

    return project?.createdBy === userId;
  } catch (error) {
    console.error(`[verifyProjectOwnership] Error:`, error);
    return false;
  }
}

/**
 * Get all projects user has access to
 *
 * @param userId - User ID
 * @returns Array of project IDs
 */
export async function getUserProjects(userId: number): Promise<string[]> {
  const db = await getDb();
  if (!db) return [];

  try {
    const [user] = await db
      .select({ role: users.role })
      .from(users)
      .where(eq(users.id, userId))
      .limit(1);

    if (!user) return [];

    // Admins see all projects
    if (user.role === "admin") {
      const allProjects = await db
        .select({ id: projects.id })
        .from(projects);
      return allProjects.map((p) => p.id);
    }

    // Get owned projects
    const ownedProjects = await db
      .select({ id: projects.id })
      .from(projects)
      .where(eq(projects.createdBy, userId));

    // Get collaborated projects
    const collaboratedProjects = await db
      .select({ projectId: projectCollaborators.projectId })
      .from(projectCollaborators)
      .where(
        and(
          eq(projectCollaborators.userId, userId),
          eq(projectCollaborators.status, "active")
        )
      );

    return [
      ...ownedProjects.map((p) => p.id),
      ...collaboratedProjects.map((c) => c.projectId),
    ];
  } catch (error) {
    console.error(`[getUserProjects] Error:`, error);
    return [];
  }
}
