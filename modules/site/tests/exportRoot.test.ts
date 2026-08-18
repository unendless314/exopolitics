import { describe, it, expect } from 'vitest';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { makeTempDir, writeJson } from './helpers/tempDir';
import {
  resolveExportBase,
  resolveExportContext,
  resolveExportRoot,
  workspaceRoot,
  EXPORT_ROOT_ENV_VAR,
} from '../src/utils/export_root.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.join(__dirname, 'fixtures', 'publish_export');

// The committed fixture's generation (derived from its stats.json
// last_export_run_timestamp, exactly like the publish migration does).
const FIXTURE_GENERATION = '2026-07-22T03-00-00Z';

const GENERATION_ID = '2026-08-01T12-00-00Z';

function validPointer(overrides: Record<string, unknown> = {}) {
  return {
    generation: GENERATION_ID,
    export_completed_at: '2026-08-01T12:00:00Z',
    last_successful_run_at: '2026-08-01T13:30:00Z',
    languages: ['zh', 'en', 'ja'],
    // Format-valid placeholder: only the publish module computes real
    // fingerprints; the site validates the shape and never recomputes it.
    content_fingerprint: `sha256-exportstate-v1:${'0'.repeat(64)}`,
    ...overrides,
  };
}

/** Minimal valid export root: a pointer plus the generation directory. */
function makeExportRoot(dir: string, pointerOverrides: Record<string, unknown> = {}) {
  const pointer = validPointer(pointerOverrides);
  // Only create the generation directory when the id is well-formed — a
  // malformed id must never be joined into a path, even in tests.
  if (/^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$/.test(pointer.generation)) {
    fs.mkdirSync(path.join(dir, 'generations', pointer.generation), { recursive: true });
  }
  writeJson(path.join(dir, 'current.json'), pointer);
}

describe('resolveExportBase', () => {
  it('returns the workspace default when the override is not set', () => {
    expect(resolveExportBase({})).toBe(path.join(workspaceRoot, 'data', 'publish_export'));
  });

  it('treats a blank override as not set', () => {
    expect(resolveExportBase({ [EXPORT_ROOT_ENV_VAR]: '   ' })).toBe(
      path.join(workspaceRoot, 'data', 'publish_export'),
    );
  });

  it('resolves an explicit override to an absolute path', () => {
    expect(resolveExportBase({ [EXPORT_ROOT_ENV_VAR]: fixtureRoot })).toBe(fixtureRoot);
  });

  it('throws a clear error when the override directory does not exist', () => {
    expect(() =>
      resolveExportBase({ [EXPORT_ROOT_ENV_VAR]: path.join(fixtureRoot, 'does-not-exist') }),
    ).toThrow(/does not exist/);
  });

  it('throws when the override points at a file, not a directory', () => {
    const tmp = makeTempDir();
    try {
      const file = path.join(tmp.dir, 'not-a-dir');
      fs.writeFileSync(file, 'x', 'utf8');
      expect(() => resolveExportBase({ [EXPORT_ROOT_ENV_VAR]: file })).toThrow(/not a directory/);
    } finally {
      tmp.cleanup();
    }
  });

  it('never falls back to the default root when an override is set but invalid', () => {
    const env = { [EXPORT_ROOT_ENV_VAR]: path.join(fixtureRoot, 'missing-again') };
    expect(() => resolveExportBase(env)).toThrow();
  });
});

describe('resolveExportContext', () => {
  it('resolves the live generation through the pointer', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir);
      const { generationRoot, pointer } = resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir });
      expect(generationRoot).toBe(path.join(tmp.dir, 'generations', GENERATION_ID));
      expect(pointer).toEqual(validPointer());
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails when current.json is missing', () => {
    const tmp = makeTempDir();
    try {
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /Missing or unreadable publish export pointer/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on unparseable JSON', () => {
    const tmp = makeTempDir();
    try {
      fs.writeFileSync(path.join(tmp.dir, 'current.json'), '{ not json', 'utf8');
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /Failed to parse publish export pointer/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on a non-object pointer', () => {
    const tmp = makeTempDir();
    try {
      writeJson(path.join(tmp.dir, 'current.json'), ['not', 'an', 'object']);
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /top-level value must be an object/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails when a required field is missing', () => {
    const tmp = makeTempDir();
    try {
      const pointer = validPointer() as Record<string, unknown>;
      delete pointer.content_fingerprint;
      fs.mkdirSync(path.join(tmp.dir, 'generations', GENERATION_ID), { recursive: true });
      writeJson(path.join(tmp.dir, 'current.json'), pointer);
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'content_fingerprint' must be/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on a malformed generation id', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { generation: '2026-08-01' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'generation' must match/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on a path-traversal generation id without touching the filesystem', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { generation: '../../outside' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'generation' must match/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails when the generation directory does not exist', () => {
    const tmp = makeTempDir();
    try {
      // Pointer is well-formed but points at a generation that was never built.
      writeJson(path.join(tmp.dir, 'current.json'), validPointer());
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /does not exist or is not a directory/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on malformed timestamps', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { last_successful_run_at: 'yesterday' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'last_successful_run_at' must be a calendar-valid ISO-8601 UTC timestamp/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on calendar-impossible timestamps instead of rolling over', () => {
    // JavaScript would silently display 2026-02-30 as March 2; the pointer
    // contract requires fail-stop, mirroring the publish-side validator.
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { export_completed_at: '2026-02-30T12:00:00Z' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'export_completed_at' must be a calendar-valid ISO-8601 UTC timestamp/,
      );
      makeExportRoot(tmp.dir, { last_successful_run_at: '2026-08-01T24:00:00Z' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'last_successful_run_at' must be a calendar-valid ISO-8601 UTC timestamp/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails when languages is not a non-empty list of strings', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { languages: 'zh' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'languages' must be a non-empty list of strings/,
      );
      // An empty list must not be accepted as the authoritative language set.
      makeExportRoot(tmp.dir, { languages: [] });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'languages' must be a non-empty list of strings/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('hard-fails on a malformed fingerprint', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { content_fingerprint: 'deadbeef' });
      expect(() => resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toThrow(
        /'content_fingerprint' must be/,
      );
    } finally {
      tmp.cleanup();
    }
  });

  it('accepts a generation id with a same-second suffix', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir, { generation: `${GENERATION_ID}-r2` });
      const { generationRoot } = resolveExportContext({ [EXPORT_ROOT_ENV_VAR]: tmp.dir });
      expect(generationRoot).toBe(path.join(tmp.dir, 'generations', `${GENERATION_ID}-r2`));
    } finally {
      tmp.cleanup();
    }
  });
});

describe('resolveExportRoot', () => {
  it('returns the live generation root resolved through the pointer', () => {
    const tmp = makeTempDir();
    try {
      makeExportRoot(tmp.dir);
      expect(resolveExportRoot({ [EXPORT_ROOT_ENV_VAR]: tmp.dir })).toBe(
        path.join(tmp.dir, 'generations', GENERATION_ID),
      );
    } finally {
      tmp.cleanup();
    }
  });
});

describe('committed development fixture layout', () => {
  const LOCALES = ['zh', 'en', 'ja'];
  const generationRoot = path.join(fixtureRoot, 'generations', FIXTURE_GENERATION);

  it('contains a valid pointer referencing the committed generation', () => {
    const { generationRoot: resolved, pointer } = resolveExportContext({
      [EXPORT_ROOT_ENV_VAR]: fixtureRoot,
    });
    expect(resolved).toBe(generationRoot);
    expect(pointer.generation).toBe(FIXTURE_GENERATION);
    expect(pointer.languages).toEqual(LOCALES);
    expect(pointer.export_completed_at).toBe('2026-07-22T03:00:00Z');
  });

  it('contains stats.json, meta.json and a complete per-locale directory structure', () => {
    expect(fs.existsSync(path.join(generationRoot, 'stats.json'))).toBe(true);
    expect(fs.existsSync(path.join(generationRoot, 'meta.json'))).toBe(true);
    for (const locale of LOCALES) {
      expect(fs.existsSync(path.join(generationRoot, locale, 'index.json')), locale).toBe(true);
      expect(
        fs.existsSync(path.join(generationRoot, locale, 'archives', 'index.json')),
        locale,
      ).toBe(true);
      expect(fs.existsSync(path.join(generationRoot, locale, 'items')), locale).toBe(true);
    }
  });

  it('records real sha256 aggregate hashes in meta.json', () => {
    // Guards against fixture drift: any edit to an aggregate file must be
    // accompanied by a meta.json refresh (same rule as the publish writer).
    const meta = JSON.parse(fs.readFileSync(path.join(generationRoot, 'meta.json'), 'utf8'));
    const entries = Object.entries(meta.aggregate_file_hashes as Record<string, string>);
    expect(entries.length).toBeGreaterThan(0);
    for (const [relPath, recorded] of entries) {
      expect(relPath, relPath).not.toContain('/items/');
      const digest = crypto
        .createHash('sha256')
        .update(fs.readFileSync(path.join(generationRoot, relPath)))
        .digest('hex');
      expect(recorded, relPath).toBe(`sha256:${digest}`);
    }
  });
});
