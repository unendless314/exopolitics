import { resolveExportRoot, workspaceRoot } from './export_root.js';

export { workspaceRoot };

/**
 * Absolute path to the publish_export data directory, resolved through the
 * shared export-root policy (src/utils/export_root.js). Honors the
 * SITE_PUBLISH_EXPORT_DIR override; otherwise the workspace default.
 */
export const publishExportDir = resolveExportRoot();
