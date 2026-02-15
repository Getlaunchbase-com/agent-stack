/**
 * verifyProjectAccess — Check if user has access to a project
 *
 * SECURITY MODEL:
 * - Project owner (createdBy) has full access
 * - Active collaborators have read access
 * - Admin role has access to all projects
 * - All other users denied
 * - Fails closed: deny on any error
 *
 * Import paths follow launchbase-platform conventions:
 *   getDb        → ../db   (server/db module)
 *   schema tables → ../drizzle/schema
 */

import { getDb } from "../db";
import { users, projects, projectCollaborators } from "../drizzle/schema";
import { eq, and } from "drizzle-orm";

/**
 * Verify user has read-level access to a project.
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
    const [user] = await db
      .select({ role: users.role })
      .from(users)
      .where(eq(users.id, userId))
      .limit(1);

    if (!user) {
      console.warn(`[verifyProjectAccess] User not found: ${userId}`);
      return false;
    }

    if (user.role === "admin") {
      return true;
    }

    const [project] = await db
      .select({ createdBy: projects.createdBy })
      .from(projects)
      .where(eq(projects.id, projectId))
      .limit(1);

    if (!project) {
      console.warn(`[verifyProjectAccess] Project not found: ${projectId}`);
      return false;
    }

    if (project.createdBy === userId) {
      return true;
    }

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

    console.warn(
      `[verifyProjectAccess] Access denied: user=${userId} project=${projectId}`
    );
    return false;
  } catch (error) {
    console.error(`[verifyProjectAccess] Error:`, error);
    return false; // Fail closed
  }
}

/**
 * Verify user owns a project (stricter than read access).
 */
export async function verifyProjectOwnership(
  userId: number,
  projectId: string
): Promise<boolean> {
  const db = await getDb();
  if (!db) return false;

  try {
    const [user] = await db
      .select({ role: users.role })
      .from(users)
      .where(eq(users.id, userId))
      .limit(1);

    if (!user) return false;
    if (user.role === "admin") return true;

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
 * Get all project IDs a user has access to.
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

    if (user.role === "admin") {
      const allProjects = await db.select({ id: projects.id }).from(projects);
      return allProjects.map((p) => p.id);
    }

    const ownedProjects = await db
      .select({ id: projects.id })
      .from(projects)
      .where(eq(projects.createdBy, userId));

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
