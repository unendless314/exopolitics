/** Absolute path to the monorepo workspace root. */
export const workspaceRoot: string;

/** Environment variable that overrides the publish export root. */
export const EXPORT_ROOT_ENV_VAR: string;

/**
 * Resolve the publish_export root directory. Returns the workspace default
 * unless SITE_PUBLISH_EXPORT_DIR is set; an explicit override must point to
 * an existing directory or this throws.
 */
export function resolveExportRoot(env?: Record<string, string | undefined>): string;
