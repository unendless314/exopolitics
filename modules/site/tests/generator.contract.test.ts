import { describe, it, expect, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { makeTempDir, writeJson, type TempDir } from './helpers/tempDir';
import { generatePosts } from '../scripts/lib/generate_posts_core.js';

/**
 * End-to-end contract tests for the generator core. Everything runs inside
 * temporary directories — the real workspace data/publish_export and
 * src/content/posts/generated are never touched.
 */

const LABELS = {
  en: { key_claim: 'Key Claim', evidence_level: 'Evidence Level', objective_impact: 'Objective Impact' },
  zh: { key_claim: '關鍵主張', evidence_level: '證據等級', objective_impact: '客觀影響' },
  ja: { key_claim: '主要主張', evidence_level: '証拠レベル', objective_impact: '客観的影響' },
};
const LOCALES = ['en', 'zh', 'ja'];

const silentLogger = { log: () => {} };

let temps: TempDir[] = [];
function tempDir(): TempDir {
  const t = makeTempDir();
  temps.push(t);
  return t;
}
afterEach(() => {
  for (const t of temps) t.cleanup();
  temps = [];
});

interface Paths {
  exportBaseDir: string;
  generatedDir: string;
  translationMapFile: string;
  labelsPath: string;
}

function setup(root: string): Paths {
  return {
    exportBaseDir: path.join(root, 'publish_export'),
    generatedDir: path.join(root, 'out', 'generated'),
    translationMapFile: path.join(root, 'out', '_translation_map.json'),
    labelsPath: path.join(root, 'post_labels.json'),
  };
}

function validItem(lang: string, slug: string, overrides: Record<string, unknown> = {}) {
  return {
    source_item_id: 1,
    language_code: lang,
    slug,
    display_title: `Title for ${slug} (${lang})`,
    summary_short: `Summary for ${slug} (${lang}).`,
    bullets: {
      key_claim: `Claim (${lang})`,
      evidence_level: `Evidence (${lang})`,
      objective_impact: `Impact (${lang})`,
    },
    canonical_url: `https://example.com/${slug}`,
    source_published_at: '2026-07-20T14:30:00Z',
    approved_at: '2026-07-21T09:05:00Z',
    published_at: '2026-07-22T03:00:00Z',
    downstream_action: 'publish_summary',
    disclosure_note: 'AI-assisted.',
    author_metadata: { source_module: 'curate', writer_type: 'machine' },
    ...overrides,
  };
}

function writeItem(paths: Paths, lang: string, item: Record<string, unknown>) {
  writeJson(path.join(paths.exportBaseDir, lang, 'items', `${item.slug as string}.json`), item);
}

function buildCompleteExport(paths: Paths, slugs: string[], labels = LABELS) {
  writeJson(paths.labelsPath, labels);
  for (const lang of Object.keys(labels)) {
    for (const slug of slugs) {
      writeItem(paths, lang, validItem(lang, slug));
    }
  }
}

function run(paths: Paths) {
  return generatePosts({ ...paths, logger: silentLogger });
}

describe('generatePosts core: happy path', () => {
  it('generates Markdown per locale/slug and a merged, sorted translation map', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post', 'beta-post']);

    const result = run(paths);
    expect(result.itemCount).toBe(6);

    for (const lang of LOCALES) {
      for (const slug of ['alpha-post', 'beta-post']) {
        const mdPath = path.join(paths.generatedDir, lang, `${slug}.md`);
        expect(fs.existsSync(mdPath), mdPath).toBe(true);
        const markdown = fs.readFileSync(mdPath, 'utf8');
        expect(markdown).toContain(`title: "Title for ${slug} (${lang})"`);
        expect(markdown).toContain(`Summary for ${slug} (${lang}).`);
      }
    }
    // Locale-specific labels land in the body.
    const zhMarkdown = fs.readFileSync(path.join(paths.generatedDir, 'zh', 'alpha-post.md'), 'utf8');
    expect(zhMarkdown).toContain('* **關鍵主張**: Claim (zh)');

    const map = JSON.parse(fs.readFileSync(paths.translationMapFile, 'utf8'));
    expect(map).toEqual({
      'alpha-post': ['en', 'ja', 'zh'],
      'beta-post': ['en', 'ja', 'zh'],
    });
  });

  it('sorts translation map locales regardless of labels config key order', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    // Deliberately non-alphabetical, non-default insertion order.
    const labels = { zh: LABELS.zh, ja: LABELS.ja, en: LABELS.en };
    buildCompleteExport(paths, ['alpha-post'], labels);

    run(paths);

    const map = JSON.parse(fs.readFileSync(paths.translationMapFile, 'utf8'));
    expect(map['alpha-post']).toEqual(['en', 'ja', 'zh']);
  });

  it('handles publish_link items (bullets: null) with summary-only bodies', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    writeJson(paths.labelsPath, LABELS);
    for (const lang of LOCALES) {
      writeItem(paths, lang, validItem(lang, 'link-post', { bullets: null }));
    }

    run(paths);

    const markdown = fs.readFileSync(path.join(paths.generatedDir, 'en', 'link-post.md'), 'utf8');
    expect(markdown).not.toContain('**Key Claim**');
  });

  it('handles the prototype-named slug "constructor" like any other slug', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    // "constructor" matches the slug contract, but a plain {} map would
    // resolve it to Object.prototype.constructor and crash on .push().
    buildCompleteExport(paths, ['constructor', 'ordinary-post']);

    const result = run(paths);
    expect(result.itemCount).toBe(6);

    const map = JSON.parse(fs.readFileSync(paths.translationMapFile, 'utf8'));
    for (const slug of ['constructor', 'ordinary-post']) {
      expect(map[slug], slug).toEqual(['en', 'ja', 'zh']);
      expect(fs.existsSync(path.join(paths.generatedDir, 'en', `${slug}.md`)), slug).toBe(true);
    }
  });
});

describe('generatePosts core: rebuild semantics', () => {
  it('rebuild clears stale Markdown and stale translation map entries', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['old-post']);
    run(paths);
    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'old-post.md'))).toBe(true);

    // Simulate a new export where old-post is gone and new-post appears.
    for (const lang of LOCALES) {
      fs.rmSync(path.join(paths.exportBaseDir, lang, 'items', 'old-post.json'));
      writeItem(paths, lang, validItem(lang, 'new-post'));
    }
    run(paths);

    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'old-post.md'))).toBe(false);
    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'new-post.md'))).toBe(true);
    const map = JSON.parse(fs.readFileSync(paths.translationMapFile, 'utf8'));
    expect(map).toEqual({ 'new-post': ['en', 'ja', 'zh'] });
  });
});

describe('generatePosts core: failure policy (no partial promotion)', () => {
  /** Pre-create a promoted artifact; after a failed run it must be intact. */
  function seedPreviousOutput(paths: Paths) {
    fs.mkdirSync(path.join(paths.generatedDir, 'en'), { recursive: true });
    fs.writeFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'previous output', 'utf8');
    fs.writeFileSync(paths.translationMapFile, '{"sentinel":["en"]}', 'utf8');
  }

  function expectPreviousOutputIntact(paths: Paths) {
    expect(fs.readFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(fs.readFileSync(paths.translationMapFile, 'utf8')).toBe('{"sentinel":["en"]}');
    // No staging leftovers.
    const outDir = path.dirname(paths.generatedDir);
    const leftovers = fs.readdirSync(outDir).filter((name) => name.includes('staging'));
    expect(leftovers).toEqual([]);
  }

  it('fails when a configured locale language directory is missing', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    fs.rmSync(path.join(paths.exportBaseDir, 'ja'), { recursive: true });
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/completeness failure/);
    expectPreviousOutputIntact(paths);
  });

  it('fails when a locale items directory is missing', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    fs.rmSync(path.join(paths.exportBaseDir, 'zh', 'items'), { recursive: true });
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/completeness failure/);
    expectPreviousOutputIntact(paths);
  });

  it('fails on a malformed item JSON without promoting partial output', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    const badFile = path.join(paths.exportBaseDir, 'en', 'items', 'broken.json');
    fs.writeFileSync(badFile, '{ not json', 'utf8');
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/reading or parsing/);
    expectPreviousOutputIntact(paths);
  });

  it('fails on an invalid item (unsafe slug) without promoting partial output', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    // Fixed file name; the traversal payload lives in the slug field.
    writeJson(
      path.join(paths.exportBaseDir, 'en', 'items', 'unsafe-slug.json'),
      validItem('en', '../traversal'),
    );
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/failed validation/);
    expectPreviousOutputIntact(paths);
  });

  it('fails on a locale mismatch without promoting partial output', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    writeItem(paths, 'zh', validItem('zh', 'mismatched-post', { language_code: 'en' }));
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/failed validation/);
    expectPreviousOutputIntact(paths);
  });

  it('fails on a duplicate slug within one locale instead of overwriting Markdown', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    // Same slug, different file name: the adapter output path would collide.
    writeJson(
      path.join(paths.exportBaseDir, 'en', 'items', 'alpha-post-copy.json'),
      validItem('en', 'alpha-post'),
    );
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/Duplicate slug/);
    expectPreviousOutputIntact(paths);
  });

  it('fails when the labels config is unusable', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    fs.writeFileSync(paths.labelsPath, '{ nope', 'utf8');
    seedPreviousOutput(paths);

    expect(() => run(paths)).toThrow(/post labels/);
    expectPreviousOutputIntact(paths);
  });

  it('keeps the live directory intact when stale-backup cleanup fails', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    seedPreviousOutput(paths);

    // A stale backup left by an earlier run sits at exactly the path the core
    // will try to clean first; make that cleanup fail.
    const backupDir = `${paths.generatedDir}.backup-${process.pid}`;
    fs.mkdirSync(backupDir, { recursive: true });
    fs.writeFileSync(path.join(backupDir, 'stale.md'), 'stale output', 'utf8');

    const originalRmSync = fs.rmSync;
    const spy = vi.spyOn(fs, 'rmSync').mockImplementation((target: any, options?: any) => {
      if (String(target) === backupDir) {
        throw new Error('simulated stale-backup cleanup failure');
      }
      return originalRmSync(target, options);
    });
    try {
      expect(() => run(paths)).toThrow(/simulated stale-backup cleanup failure/);
    } finally {
      spy.mockRestore();
    }

    // The live tree was never moved aside this run, so it must survive as-is;
    // the stale backup must not be promoted over it.
    expect(fs.readFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(fs.existsSync(path.join(paths.generatedDir, 'stale.md'))).toBe(false);
    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'alpha-post.md'))).toBe(false);
    expect(fs.readFileSync(paths.translationMapFile, 'utf8')).toBe('{"sentinel":["en"]}');
  });

  it('restores the old pair when map promotion fails', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    seedPreviousOutput(paths);

    // Fail only the staging-map rename; the map backup step (target is the
    // backup path) and the rollback restore rename must pass through.
    const originalRename = fs.renameSync;
    let failedOnce = false;
    const spy = vi.spyOn(fs, 'renameSync').mockImplementation((from: any, to: any) => {
      if (String(to) === paths.translationMapFile && !failedOnce) {
        failedOnce = true;
        throw new Error('simulated map promotion failure');
      }
      return originalRename(from, to);
    });
    try {
      expect(() => run(paths)).toThrow(/simulated map promotion failure/);
    } finally {
      spy.mockRestore();
    }

    // Map restored from its backup; directory was never touched.
    expect(fs.readFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(fs.readFileSync(paths.translationMapFile, 'utf8')).toBe('{"sentinel":["en"]}');
    const leftovers = fs
      .readdirSync(path.dirname(paths.generatedDir))
      .filter((name) => name.includes('staging') || name.includes('backup'));
    expect(leftovers).toEqual([]);
  });

  it('restores the old pair when directory promotion fails', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    seedPreviousOutput(paths);

    // Fail only the staging-directory rename; the backup moves and the
    // rollback restores must pass through.
    const originalRename = fs.renameSync;
    let failedOnce = false;
    const spy = vi.spyOn(fs, 'renameSync').mockImplementation((from: any, to: any) => {
      if (String(to) === paths.generatedDir && !failedOnce) {
        failedOnce = true;
        throw new Error('simulated directory promotion failure');
      }
      return originalRename(from, to);
    });
    try {
      expect(() => run(paths)).toThrow(/simulated directory promotion failure/);
    } finally {
      spy.mockRestore();
    }

    // Both artifacts restored to the previous pair.
    expect(fs.readFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'alpha-post.md'))).toBe(false);
    expect(fs.readFileSync(paths.translationMapFile, 'utf8')).toBe('{"sentinel":["en"]}');
    const leftovers = fs
      .readdirSync(path.dirname(paths.generatedDir))
      .filter((name) => name.includes('staging') || name.includes('backup'));
    expect(leftovers).toEqual([]);
  });

  it('keeps the new pair live when post-commit backup cleanup fails', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    seedPreviousOutput(paths);
    const backupDir = `${paths.generatedDir}.backup-${process.pid}`;

    // The pre-commit cleanup (first call) must succeed; the post-commit
    // cleanup (second call) fails. Housekeeping must not roll back the commit.
    const originalRmSync = fs.rmSync;
    let backupRmCalls = 0;
    const spy = vi.spyOn(fs, 'rmSync').mockImplementation((target: any, options?: any) => {
      if (String(target) === backupDir) {
        backupRmCalls += 1;
        if (backupRmCalls > 1) throw new Error('simulated backup cleanup failure');
      }
      return originalRmSync(target, options);
    });
    const messages: string[] = [];
    const logger = { log: (msg: string) => messages.push(msg) };
    try {
      generatePosts({ ...paths, logger });
    } finally {
      spy.mockRestore();
    }

    // New pair stays live; the backup is retained and a warning was logged.
    expect(fs.existsSync(path.join(paths.generatedDir, 'en', 'alpha-post.md'))).toBe(true);
    const map = JSON.parse(fs.readFileSync(paths.translationMapFile, 'utf8'));
    expect(map['alpha-post']).toEqual(['en', 'ja', 'zh']);
    expect(fs.readFileSync(path.join(backupDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(messages.some((msg) => /could not remove backup/.test(msg))).toBe(true);
  });

  it('reports both errors and retains backups when rollback itself fails', () => {
    const tmp = tempDir();
    const paths = setup(tmp.dir);
    buildCompleteExport(paths, ['alpha-post']);
    seedPreviousOutput(paths);
    const backupMapFile = `${paths.translationMapFile}.backup-${process.pid}`;

    // Transaction failure: directory promotion fails once. Rollback failure:
    // removing the promoted map fails too, so the old map stays in its backup.
    const originalRename = fs.renameSync;
    let failedOnce = false;
    const renameSpy = vi.spyOn(fs, 'renameSync').mockImplementation((from: any, to: any) => {
      if (String(to) === paths.generatedDir && !failedOnce) {
        failedOnce = true;
        throw new Error('simulated directory promotion failure');
      }
      return originalRename(from, to);
    });
    const originalRmSync = fs.rmSync;
    const rmSpy = vi.spyOn(fs, 'rmSync').mockImplementation((target: any, options?: any) => {
      if (String(target) === paths.translationMapFile) {
        throw new Error('simulated map removal failure');
      }
      return originalRmSync(target, options);
    });
    try {
      expect(() => run(paths)).toThrow(/promotion failed[\s\S]*Rollback also failed/);
    } finally {
      renameSpy.mockRestore();
      rmSpy.mockRestore();
    }

    // The directory was restored, but the map rollback failure is surfaced
    // and its backup retained for manual recovery — not silently suppressed.
    expect(fs.readFileSync(path.join(paths.generatedDir, 'en', 'sentinel.md'), 'utf8')).toBe(
      'previous output',
    );
    expect(fs.existsSync(backupMapFile)).toBe(true);
  });
});
