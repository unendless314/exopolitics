import { describe, it, expect, afterEach } from 'vitest';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

import { makeTempDir, writeJson, type TempDir } from './helpers/tempDir';
import { localeProfiles } from '../src/utils/i18n';
import { resolveExportContext, EXPORT_ROOT_ENV_VAR } from '../src/utils/export_root.js';
import {
  validateCatalogItem,
  validateArchiveManifestEntry,
  validateStats,
} from '../src/utils/validation';
import {
  loadLanguageIndex,
  loadArchiveManifest,
  loadArchiveMonth,
  loadStats,
} from '../src/utils/exportData';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// The committed fixture is a generationized export root (Phase B1): the
// loader matrix runs against the live generation resolved through its
// current.json pointer, exactly like production pages do.
const committedFixtureBase = path.join(__dirname, 'fixtures', 'publish_export');
const committedFixtureRoot = resolveExportContext({
  [EXPORT_ROOT_ENV_VAR]: committedFixtureBase,
}).generationRoot;
const REQUIRED_LOCALES = Object.keys(localeProfiles);

const ERROR_TAG = /Data Integrity Validation Failed/;

function validCatalogItem(overrides: Record<string, unknown> = {}) {
  return {
    slug: 'sample-slug',
    display_title: 'Sample title',
    summary_short: 'Sample summary.',
    canonical_url: 'https://example.com/sample',
    source_published_at: '2026-07-20T14:30:00Z',
    approved_at: '2026-07-21T09:05:00Z',
    published_at: '2026-07-22T03:00:00Z',
    ...overrides,
  };
}

function validManifestEntry(overrides: Record<string, unknown> = {}) {
  return {
    archive_month: '2026-07',
    file_name: 'archive_2026_07.json',
    item_count: 2,
    updated_at: '2026-07-22T03:00:00Z',
    ...overrides,
  };
}

function validStats(overrides: Record<string, unknown> = {}) {
  const perLocale = (value: unknown) => Object.fromEntries(REQUIRED_LOCALES.map((l) => [l, value]));
  return {
    total_active_published_items_by_language: perLocale(2),
    total_withdrawn_items_by_language: perLocale(0),
    latest_index_count_by_language: perLocale(2),
    archive_month_count_by_language: perLocale(1),
    oldest_archive_month_by_language: perLocale('2026-07'),
    last_export_run_timestamp: '2026-07-22T03:00:00Z',
    ...overrides,
  };
}

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

describe('validateCatalogItem', () => {
  it('accepts a valid catalog item and maps it to the site metadata model', () => {
    const raw = validCatalogItem();
    const validated = validateCatalogItem(raw, 'test');
    expect(validated).toEqual(raw);
  });

  const rejectionCases: Array<[string, (item: any) => void]> = [
    ['missing slug', (item) => { delete item.slug; }],
    ['blank slug', (item) => { item.slug = '   '; }],
    ['non-string slug', (item) => { item.slug = 42; }],
    ['missing display_title', (item) => { delete item.display_title; }],
    ['blank display_title', (item) => { item.display_title = ' \n\t '; }],
    ['non-string display_title', (item) => { item.display_title = null; }],
    ['missing summary_short', (item) => { delete item.summary_short; }],
    ['blank summary_short', (item) => { item.summary_short = '  '; }],
    ['non-string summary_short', (item) => { item.summary_short = 7; }],
    ['unparseable source_published_at', (item) => { item.source_published_at = 'not-a-date'; }],
    ['missing source_published_at', (item) => { delete item.source_published_at; }],
  ];
  for (const [caseName, mutate] of rejectionCases) {
    it(`rejects an item with ${caseName}`, () => {
      const item = validCatalogItem();
      mutate(item);
      expect(() => validateCatalogItem(item, 'test')).toThrow(ERROR_TAG);
    });
  }

  it('rejects a missing summary_short even when display_title is present (no fallback)', () => {
    const item = validCatalogItem();
    delete (item as any).summary_short;
    expect(() => validateCatalogItem(item, 'test')).toThrow(/summary_short/);
  });

  it('rejects catalog slugs that violate the handoff slug format', () => {
    // Same contract as detail items: unsafe slugs must not reach URL construction.
    for (const slug of ['../x', 'a/b', 'a\\b', '..', 'Slug', '-slug', ' slug']) {
      const item = validCatalogItem({ slug });
      expect(() => validateCatalogItem(item, 'test'), slug).toThrow(/slug format/);
    }
    for (const slug of ['item', 'item-2', '2026']) {
      expect(() => validateCatalogItem(validCatalogItem({ slug }), 'test'), slug).not.toThrow();
    }
  });

  it('rejects non-object items', () => {
    for (const bad of [null, 'text', 5, [1, 2]]) {
      expect(() => validateCatalogItem(bad, 'test')).toThrow(ERROR_TAG);
    }
  });
});

describe('validateArchiveManifestEntry', () => {
  it('accepts a valid manifest entry', () => {
    expect(validateArchiveManifestEntry(validManifestEntry(), 'test')).toEqual(validManifestEntry());
  });

  const rejectionCases: Array<[string, Record<string, unknown>]> = [
    ['a missing archive_month', { archive_month: undefined }],
    ['a malformed archive_month', { archive_month: '2026-7-01' }],
    ['a missing file_name', { file_name: '  ' }],
    ['a negative item_count', { item_count: -1 }],
    ['a non-integer item_count', { item_count: 1.5 }],
    ['an unparseable updated_at', { updated_at: 'tomorrow' }],
    ['a traversal file_name', { file_name: '../../zh/index.json' }],
    ['a backslash file_name', { file_name: '..\\..\\zh\\index.json' }],
    ['an absolute-path file_name', { file_name: '/etc/passwd.json' }],
    ['a dot-segment file_name', { file_name: '..' }],
    ['a non-json file_name', { file_name: 'archive_2026_07.txt' }],
  ];
  for (const [caseName, overrides] of rejectionCases) {
    it(`rejects an entry with ${caseName}`, () => {
      const entry = validManifestEntry();
      for (const [key, value] of Object.entries(overrides)) {
        if (value === undefined) delete (entry as any)[key];
        else (entry as any)[key] = value;
      }
      expect(() => validateArchiveManifestEntry(entry, 'test')).toThrow(ERROR_TAG);
    });
  }
});

describe('validateStats', () => {
  it('accepts a valid stats payload', () => {
    expect(validateStats(validStats(), REQUIRED_LOCALES)).toEqual(validStats());
  });

  it('accepts null oldest_archive_month for a locale without archives (temporary allowance)', () => {
    const stats = validStats({
      oldest_archive_month_by_language: { zh: null, en: '2026-07', ja: '2026-07' },
    });
    expect(() => validateStats(stats, REQUIRED_LOCALES)).not.toThrow();
  });

  it('tolerates extra unknown locale keys from upstream', () => {
    const stats = validStats();
    (stats.total_active_published_items_by_language as any).fr = 5;
    (stats.oldest_archive_month_by_language as any).fr = '2026-07';
    expect(() => validateStats(stats, REQUIRED_LOCALES)).not.toThrow();
  });

  const rejectionCases: Array<[string, (stats: any) => void]> = [
    ['an unparseable last_export_run_timestamp', (s) => { s.last_export_run_timestamp = 'n/a'; }],
    ['a missing count map', (s) => { delete s.latest_index_count_by_language; }],
    ['a missing oldest_archive_month map', (s) => { delete s.oldest_archive_month_by_language; }],
    ['a negative count', (s) => { s.total_active_published_items_by_language.en = -1; }],
    ['a non-integer count', (s) => { s.total_withdrawn_items_by_language.zh = 0.5; }],
    ['a missing configured locale in a count map', (s) => { delete s.archive_month_count_by_language.ja; }],
    ['a missing configured locale in the oldest-month map', (s) => { delete s.oldest_archive_month_by_language.zh; }],
    ['a malformed oldest month', (s) => { s.oldest_archive_month_by_language.en = 'July 2026'; }],
  ];
  for (const [caseName, mutate] of rejectionCases) {
    it(`rejects stats with ${caseName}`, () => {
      const stats = validStats();
      mutate(stats);
      expect(() => validateStats(stats, REQUIRED_LOCALES)).toThrow(ERROR_TAG);
    });
  }
});

describe('exportData loaders', () => {
  function buildExport(root: string, lang: string) {
    writeJson(path.join(root, lang, 'index.json'), [validCatalogItem()]);
    writeJson(path.join(root, lang, 'archives', 'index.json'), [validManifestEntry()]);
    writeJson(path.join(root, lang, 'archives', 'archive_2026_07.json'), [validCatalogItem()]);
    writeJson(path.join(root, 'stats.json'), validStats());
  }

  it('loads a valid language index', () => {
    const tmp = tempDir();
    buildExport(tmp.dir, 'en');
    const items = loadLanguageIndex(tmp.dir, 'en');
    expect(items).toHaveLength(1);
    expect(items[0].slug).toBe('sample-slug');
  });

  it('hard-fails when the language index file is missing', () => {
    const tmp = tempDir();
    expect(() => loadLanguageIndex(tmp.dir, 'en')).toThrow(/Missing or unreadable/);
  });

  it('hard-fails on unparseable JSON with a wrapped error', () => {
    const tmp = tempDir();
    const file = path.join(tmp.dir, 'en', 'index.json');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, '{ not json', 'utf8');
    expect(() => loadLanguageIndex(tmp.dir, 'en')).toThrow(/Failed to parse/);
  });

  it('hard-fails when the catalog root is not an array', () => {
    const tmp = tempDir();
    writeJson(path.join(tmp.dir, 'en', 'index.json'), { items: [] });
    expect(() => loadLanguageIndex(tmp.dir, 'en')).toThrow(ERROR_TAG);
  });

  it('hard-fails on an invalid catalog item inside the file', () => {
    const tmp = tempDir();
    writeJson(path.join(tmp.dir, 'en', 'index.json'), [validCatalogItem({ summary_short: '  ' })]);
    expect(() => loadLanguageIndex(tmp.dir, 'en')).toThrow(/summary_short/);
  });

  it('loads a valid archive manifest and monthly archive', () => {
    const tmp = tempDir();
    buildExport(tmp.dir, 'ja');
    expect(loadArchiveManifest(tmp.dir, 'ja')).toHaveLength(1);
    expect(loadArchiveMonth(tmp.dir, 'ja', 'archive_2026_07.json')).toHaveLength(1);
  });

  it('hard-fails when the archive manifest or monthly file is missing', () => {
    const tmp = tempDir();
    writeJson(path.join(tmp.dir, 'ja', 'index.json'), []);
    expect(() => loadArchiveManifest(tmp.dir, 'ja')).toThrow(/Missing or unreadable/);
    expect(() => loadArchiveMonth(tmp.dir, 'ja', 'archive_2026_07.json')).toThrow(/Missing or unreadable/);
  });

  it('rejects an unsafe archive file name even when called directly', () => {
    const tmp = tempDir();
    buildExport(tmp.dir, 'ja');
    for (const bad of ['../../zh/index.json', '..\\index.json', '/abs/path.json', '..']) {
      expect(() => loadArchiveMonth(tmp.dir, 'ja', bad), bad).toThrow(/Unsafe archive file name/);
    }
  });

  it('hard-fails on an invalid manifest entry', () => {
    const tmp = tempDir();
    writeJson(path.join(tmp.dir, 'ja', 'archives', 'index.json'), [validManifestEntry({ item_count: -3 })]);
    expect(() => loadArchiveManifest(tmp.dir, 'ja')).toThrow(/item_count/);
  });

  it('loads valid stats for all configured locales', () => {
    const tmp = tempDir();
    buildExport(tmp.dir, 'en');
    const stats = loadStats(tmp.dir, REQUIRED_LOCALES);
    expect(stats.last_export_run_timestamp).toBe('2026-07-22T03:00:00Z');
  });

  it('hard-fails when stats.json is missing or invalid', () => {
    const tmp = tempDir();
    expect(() => loadStats(tmp.dir, REQUIRED_LOCALES)).toThrow(/Missing or unreadable/);
    writeJson(path.join(tmp.dir, 'stats.json'), validStats({ last_export_run_timestamp: 'soon' }));
    expect(() => loadStats(tmp.dir, REQUIRED_LOCALES)).toThrow(/last_export_run_timestamp/);
  });
});

describe('committed development fixture (tests/fixtures/publish_export)', () => {
  // The fixture represents the handoff contract: it must pass the exact same
  // loader/validator matrix as a production export, so fixture drift fails here.
  it('passes the full loader matrix for every configured locale', () => {
    for (const lang of REQUIRED_LOCALES) {
      const index = loadLanguageIndex(committedFixtureRoot, lang);
      expect(index.length, lang).toBeGreaterThan(0);

      const manifest = loadArchiveManifest(committedFixtureRoot, lang);
      expect(manifest.length, lang).toBeGreaterThan(0);
      for (const entry of manifest) {
        const monthItems = loadArchiveMonth(committedFixtureRoot, lang, entry.file_name);
        expect(monthItems.length, `${lang}/${entry.archive_month}`).toBeGreaterThan(0);
      }
    }
    const stats = loadStats(committedFixtureRoot, REQUIRED_LOCALES);
    for (const lang of REQUIRED_LOCALES) {
      expect(stats.total_active_published_items_by_language[lang]).toBeGreaterThan(0);
    }
  });

  it('has a detail item JSON on disk for every catalog slug', () => {
    for (const lang of REQUIRED_LOCALES) {
      for (const item of loadLanguageIndex(committedFixtureRoot, lang)) {
        const itemPath = path.join(committedFixtureRoot, lang, 'items', `${item.slug}.json`);
        expect(fs.existsSync(itemPath), itemPath).toBe(true);
      }
    }
  });
});
