import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveExportRoot } from '../src/utils/export_root.js';
import { generatePosts } from './lib/generate_posts_core.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// CLI entry point: resolves the formal default paths, delegates to the
// testable core, and converts failures into a process exit code.
const exportBaseDir = resolveExportRoot();
const generatedDir = path.resolve(__dirname, '..', 'src', 'content', 'posts', 'generated');
const translationMapFile = path.resolve(__dirname, '..', 'src', 'content', '_translation_map.json');

console.log(`Starting post generation...`);
console.log(`Reading from: ${exportBaseDir}`);
console.log(`Writing to: ${generatedDir}`);

try {
  generatePosts({ exportBaseDir, generatedDir, translationMapFile });
} catch (err) {
  console.error(`Post generation failed: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
}
