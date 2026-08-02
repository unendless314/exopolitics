import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { makeTempDir } from './helpers/tempDir';
import {
  resolveExportRoot,
  workspaceRoot,
  EXPORT_ROOT_ENV_VAR,
} from '../src/utils/export_root.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(__dirname, 'fixtures', 'publish_export');

describe('resolveExportRoot', () => {
  it('returns the workspace default when the override is not set', () => {
    expect(resolveExportRoot({})).toBe(path.join(workspaceRoot, 'data', 'publish_export'));
  });

  it('treats a blank override as not set', () => {
    expect(resolveExportRoot({ [EXPORT_ROOT_ENV_VAR]: '   ' })).toBe(
      path.join(workspaceRoot, 'data', 'publish_export'),
    );
  });

  it('resolves an explicit override to an absolute path', () => {
    expect(resolveExportRoot({ [EXPORT_ROOT_ENV_VAR]: fixtureRoot })).toBe(fixtureRoot);
  });

  it('throws a clear error when the override directory does not exist', () => {
    expect(() =>
      resolveExportRoot({ [EXPORT_ROOT_ENV_VAR]: path.join(fixtureRoot, 'does-not-exist') }),
    ).toThrow(/does not exist/);
  });

  it('throws when the override points at a file, not a directory', () => {
    const tmp = makeTempDir();
    try {
      const file = path.join(tmp.dir, 'not-a-dir');
      fs.writeFileSync(file, 'x', 'utf8');
      expect(() => resolveExportRoot({ [EXPORT_ROOT_ENV_VAR]: file })).toThrow(/not a directory/);
    } finally {
      tmp.cleanup();
    }
  });

  it('never falls back to the default root when an override is set but invalid', () => {
    const env = { [EXPORT_ROOT_ENV_VAR]: path.join(fixtureRoot, 'missing-again') };
    expect(() => resolveExportRoot(env)).toThrow();
  });
});

describe('committed development fixture layout', () => {
  const LOCALES = ['zh', 'en', 'ja'];

  it('contains stats.json and a complete per-locale directory structure', () => {
    expect(fs.existsSync(path.join(fixtureRoot, 'stats.json'))).toBe(true);
    for (const locale of LOCALES) {
      expect(fs.existsSync(path.join(fixtureRoot, locale, 'index.json')), locale).toBe(true);
      expect(
        fs.existsSync(path.join(fixtureRoot, locale, 'archives', 'index.json')),
        locale,
      ).toBe(true);
      expect(fs.existsSync(path.join(fixtureRoot, locale, 'items')), locale).toBe(true);
    }
  });
});
