import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Absolute path to the monorepo workspace root. */
export const workspaceRoot = path.resolve(__dirname, '..', '..', '..', '..');

/** Absolute path to the publish_export data directory. */
export const publishExportDir = path.join(workspaceRoot, 'data', 'publish_export');
