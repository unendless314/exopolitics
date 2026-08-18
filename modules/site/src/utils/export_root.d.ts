/** Absolute path to the monorepo workspace root. */
export const workspaceRoot: string;

/** Environment variable that overrides the publish export root. */
export const EXPORT_ROOT_ENV_VAR: string;

/**
 * Resolve the publish_export base directory (the directory holding
 * current.json and generations/). Returns the workspace default unless
 * SITE_PUBLISH_EXPORT_DIR is set; an explicit override must point to an
 * existing directory or this throws. Does not read the pointer.
 */
export function resolveExportBase(env?: Record<string, string | undefined>): string;

/** Validated payload of the publish export pointer (current.json). */
export interface PublishExportPointer {
  generation: string;
  export_completed_at: string;
  last_successful_run_at: string;
  languages: string[];
  content_fingerprint: string;
}

/** The live export generation as resolved through the pointer. */
export interface ExportContext {
  /** Absolute path to generations/<generation>. */
  generationRoot: string;
  /** The validated current.json payload. */
  pointer: PublishExportPointer;
}

/**
 * Resolve the live export generation through the current.json pointer.
 * Throws when the pointer is missing, unparseable or invalid, or when the
 * referenced generation directory does not exist.
 */
export function resolveExportContext(env?: Record<string, string | undefined>): ExportContext;

/**
 * Absolute path of the live export generation directory (equivalent to
 * resolveExportContext(env).generationRoot).
 */
export function resolveExportRoot(env?: Record<string, string | undefined>): string;
