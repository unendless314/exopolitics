import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Create an isolated temporary directory for filesystem tests. Returns a
 * handle whose cleanup() removes the tree recursively. Generator and loader
 * tests must only ever read/write inside directories made here — never the
 * real workspace data/publish_export or src/content/posts/generated.
 */
export function makeTempDir(prefix = 'site-test-'): TempDir {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  return {
    dir,
    cleanup() {
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

export interface TempDir {
  dir: string;
  cleanup(): void;
}

/** Write a JSON file, creating parent directories as needed. */
export function writeJson(filePath: string, value: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), 'utf8');
}
