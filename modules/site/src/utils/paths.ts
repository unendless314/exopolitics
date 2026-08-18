import { resolveExportContext, workspaceRoot } from './export_root.js';

export { workspaceRoot };

/**
 * The live publish export, resolved through the shared export-root policy
 * and the current.json pointer (src/utils/export_root.js). Honors the
 * SITE_PUBLISH_EXPORT_DIR override; otherwise the workspace default. A
 * missing or invalid pointer hard-fails at module load.
 */
const exportContext = resolveExportContext();

/**
 * Absolute path to the live generation directory. All artifact readers
 * (language index, archives, item payloads, stats) operate on this root.
 */
export const publishExportDir = exportContext.generationRoot;

/**
 * The validated current.json pointer. `last_successful_run_at` is the run
 * freshness signal ("last updated"): unlike the artifacts inside the frozen
 * generation, it advances on every successful publish run.
 */
export const publishExportPointer = exportContext.pointer;
