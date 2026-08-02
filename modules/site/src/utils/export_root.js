import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Absolute path to the monorepo workspace root. */
export const workspaceRoot = path.resolve(__dirname, '..', '..', '..', '..');

/** Environment variable that overrides the publish export root. */
export const EXPORT_ROOT_ENV_VAR = 'SITE_PUBLISH_EXPORT_DIR';

/**
 * Single export-root resolution policy shared by the generator script, the
 * Astro loaders, and the Footer stats loader.
 *
 * - When SITE_PUBLISH_EXPORT_DIR is not set (or blank), returns the workspace
 *   default `<workspace>/data/publish_export`. Existence is not checked here;
 *   missing default-root files hard-fail later at the loaders.
 * - When set, resolves the value to an absolute path (relative values resolve
 *   against process.cwd()) and throws a clear error if the directory does not
 *   exist. An explicit override must never silently fall back to the default.
 */
export function resolveExportRoot(env = process.env) {
  const override = env[EXPORT_ROOT_ENV_VAR];
  if (override && override.trim().length > 0) {
    const resolved = path.resolve(override.trim());
    let stat;
    try {
      stat = fs.statSync(resolved);
    } catch (err) {
      throw new Error(
        `${EXPORT_ROOT_ENV_VAR} points to "${resolved}", which does not exist: ${err.message}`,
      );
    }
    if (!stat.isDirectory()) {
      throw new Error(`${EXPORT_ROOT_ENV_VAR} points to "${resolved}", which is not a directory.`);
    }
    return resolved;
  }
  return path.join(workspaceRoot, 'data', 'publish_export');
}
