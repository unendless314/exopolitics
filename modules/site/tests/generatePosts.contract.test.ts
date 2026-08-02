import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { localeProfiles } from '../src/utils/i18n';
import { makeTempDir } from './helpers/tempDir';

/**
 * Contract tests for the build-time JSON-to-Markdown adapter.
 *
 *   Module: modules/site/scripts/lib/post_adapter.js (plain Node ESM, no deps)
 *   - loadPostLabels(labelsPath?) => { [locale]: { key_claim, evidence_level, objective_impact } }
 *       Reads src/config/post_labels.json by default. Throws on missing/invalid file.
 *   - validateItem(item) => void
 *       Throws unless: slug is a non-empty string matching the handoff slug
 *       contract (^[a-z0-9][a-z0-9-]*$, rejected never sanitized),
 *       display_title and language_code are non-empty strings,
 *       source_published_at parses as a date, summary_short is a non-empty
 *       string after trimming, and bullets is either null or an object with
 *       exactly the three known keys whose values are non-empty strings.
 *   - assembleMarkdown(item, locale, labels) => string
 *       Validates the item (hard failure), throws when item.language_code
 *       does not equal the requested locale, throws on unknown locale, and
 *       returns frontmatter + body. description is item.summary_short
 *       verbatim; the body is the summary paragraph plus, when bullets are
 *       present, a `* **<locale label>**: <value>` list in the fixed key
 *       order key_claim, evidence_level, objective_impact.
 *   - getAdapterLanguages(labels) => string[]
 *       Object.keys(labels); replaces the hardcoded ['en', 'ja', 'zh'] in
 *       generate-posts.js.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fixturesDir = path.join(__dirname, 'fixtures');
const postLabelsPath = path.resolve(__dirname, '..', 'src', 'config', 'post_labels.json');

const ADAPTER_MODULE = '../scripts/lib/post_adapter.js';

// One explicit locale rendering assertion, kept so the shipped label wording
// itself is pinned by a test; expectations for the other locales are derived
// from the actual label config to avoid maintaining two copies of the text.
const EXPECTED_ZH_LABELS = { key_claim: '關鍵主張', evidence_level: '證據等級', objective_impact: '客觀影響' };

function loadFixture(name: string): any {
  return JSON.parse(fs.readFileSync(path.join(fixturesDir, name), 'utf8'));
}

async function loadAdapter(): Promise<any> {
  return await import(ADAPTER_MODULE);
}

/** Extract the Markdown body after the closing frontmatter `---` line. */
function extractBody(markdown: string): string {
  const match = markdown.match(/^---\n[\s\S]*?\n---\n\n([\s\S]*)$/);
  if (!match) throw new Error('generated markdown has no frontmatter block');
  return match[1];
}

describe('generate-posts adapter contract', () => {
  describe('assembleMarkdown: localized post labels', () => {
    for (const locale of ['en', 'zh', 'ja'] as const) {
      it(`assembles ${locale} markdown with ${locale} labels`, async () => {
        const { loadPostLabels, assembleMarkdown } = await loadAdapter();
        const item = loadFixture(`item_publish_summary_${locale}.json`);
        const labels = loadPostLabels();
        // zh expectations are pinned above; other locales derive from the
        // loaded label config so wording lives in exactly one place.
        const expected = locale === 'zh' ? EXPECTED_ZH_LABELS : labels[locale];

        const markdown = assembleMarkdown(item, locale, labels);

        const expectedBody = [
          item.summary_short,
          '',
          `* **${expected.key_claim}**: ${item.bullets.key_claim}`,
          `* **${expected.evidence_level}**: ${item.bullets.evidence_level}`,
          `* **${expected.objective_impact}**: ${item.bullets.objective_impact}`,
        ].join('\n');
        expect(extractBody(markdown).trimEnd()).toBe(expectedBody);
      });
    }
  });

  describe('assembleMarkdown: publish_link (bullets: null)', () => {
    it('emits only the summary paragraph, without a bullet list', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_link_en.json');
      const labels = loadPostLabels();

      const markdown = assembleMarkdown(item, 'en', labels);
      const body = extractBody(markdown);

      expect(body.trim()).toBe(item.summary_short);
      expect(body).not.toContain('**');
    });
  });

  describe('validateItem: hard failure on invalid items', () => {
    it('accepts valid publish_summary and publish_link items', async () => {
      const { validateItem } = await loadAdapter();
      for (const name of [
        'item_publish_summary_en.json',
        'item_publish_summary_zh.json',
        'item_publish_summary_ja.json',
        'item_publish_link_en.json',
      ]) {
        expect(() => validateItem(loadFixture(name)), name).not.toThrow();
      }
    });

    for (const name of [
      'invalid_missing_summary_short.json',
      'invalid_blank_summary_short.json',
      'invalid_partial_bullets.json',
      'invalid_empty_bullets_object.json',
      'invalid_unknown_bullet_key.json',
    ]) {
      it(`rejects ${name}`, async () => {
        const { validateItem } = await loadAdapter();
        expect(() => validateItem(loadFixture(name))).toThrow();
      });
    }

    it('rejects whitespace-only bullet values', async () => {
      const { validateItem } = await loadAdapter();
      const item = loadFixture('item_publish_summary_en.json');
      item.bullets.key_claim = '   ';
      expect(() => validateItem(item)).toThrow();
    });

    // Checks folded into validateItem from the legacy generator: the fields
    // Astro frontmatter depends on must be present and usable.
    const requiredFieldCases: Array<[string, (item: any) => void]> = [
      ['a missing slug', (item) => { delete item.slug; }],
      ['a blank slug', (item) => { item.slug = '   '; }],
      ['a missing display_title', (item) => { delete item.display_title; }],
      ['a blank display_title', (item) => { item.display_title = '  \n\t  '; }],
      ['an unparseable source_published_at', (item) => { item.source_published_at = 'not-a-date'; }],
    ];
    for (const [caseName, mutate] of requiredFieldCases) {
      it(`rejects an item with ${caseName}`, async () => {
        const { validateItem } = await loadAdapter();
        const item = loadFixture('item_publish_summary_en.json');
        mutate(item);
        expect(() => validateItem(item)).toThrow();
      });
    }
  });

  describe('validateItem: slug handoff contract (^[a-z0-9][a-z0-9-]*$)', () => {
    // The site rejects non-conforming slugs at the filesystem boundary so a
    // bad value can never escape the generated output directory. Values are
    // rejected verbatim — never sanitized or rewritten.
    const unsafeSlugs: Array<[string, string]> = [
      ['a path traversal segment', '../escape'],
      ['an embedded forward slash', 'a/b'],
      ['an embedded backslash', 'a\\b'],
      ['a dot segment', '..'],
      ['a single dot', '.'],
      ['an absolute posix path', '/etc/passwd'],
      ['an absolute windows path', 'C:\\temp\\x'],
      ['leading whitespace', ' slug'],
      ['trailing whitespace', 'slug '],
      ['uppercase characters', 'Slug'],
      ['a leading hyphen', '-slug'],
      ['a trailing dot', 'slug.'],
      ['a non-ASCII character', 'slüg'],
    ];
    for (const [caseName, slug] of unsafeSlugs) {
      it(`rejects a slug with ${caseName}`, async () => {
        const { validateItem } = await loadAdapter();
        const item = loadFixture('item_publish_summary_en.json');
        item.slug = slug;
        expect(() => validateItem(item)).toThrow(/slug format/);
      });
    }

    it('accepts the publish empty-after-normalization fallbacks (item, item-N)', async () => {
      const { validateItem } = await loadAdapter();
      for (const slug of ['item', 'item-2', '2026', 'a']) {
        const item = loadFixture('item_publish_summary_en.json');
        item.slug = slug;
        expect(() => validateItem(item), slug).not.toThrow();
      }
    });
  });

  describe('validateItem: language_code', () => {
    const languageCodeCases: Array<[string, unknown]> = [
      ['missing', undefined],
      ['null', null],
      ['a non-string', 123],
      ['blank', '   '],
    ];
    for (const [caseName, value] of languageCodeCases) {
      it(`rejects an item with ${caseName} language_code`, async () => {
        const { loadPostLabels, assembleMarkdown } = await loadAdapter();
        const item = loadFixture('item_publish_summary_en.json');
        if (value === undefined) delete item.language_code;
        else item.language_code = value;
        const labels = loadPostLabels();
        expect(() => assembleMarkdown(item, 'en', labels)).toThrow(/language_code/);
      });
    }
  });

  describe('loadPostLabels: error surface', () => {
    function withLabelsFile(content: string | null, run: (labelsPath: string) => void) {
      const tmp = makeTempDir();
      try {
        const labelsPath = path.join(tmp.dir, 'post_labels.json');
        if (content !== null) fs.writeFileSync(labelsPath, content, 'utf8');
        run(labelsPath);
      } finally {
        tmp.cleanup();
      }
    }

    it('throws when the labels file is missing', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile(null, (labelsPath) => {
        expect(() => loadPostLabels(labelsPath)).toThrow(/Failed to read/);
      });
    });

    it('throws when the labels file is not parseable JSON', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile('{ nope', (labelsPath) => {
        expect(() => loadPostLabels(labelsPath)).toThrow(/Failed to parse/);
      });
    });

    it('throws when the labels map is empty', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile('{}', (labelsPath) => {
        expect(() => loadPostLabels(labelsPath)).toThrow(/no locales/);
      });
    });

    it('throws when the top-level value is not an object keyed by locale', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile('["en"]', (labelsPath) => {
        expect(() => loadPostLabels(labelsPath)).toThrow(/keyed by locale/);
      });
    });

    it('throws when a locale is missing a label key', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile(
        JSON.stringify({ en: { key_claim: 'Key Claim', evidence_level: 'Evidence Level' } }),
        (labelsPath) => {
          expect(() => loadPostLabels(labelsPath)).toThrow(/exactly the keys/);
        },
      );
    });

    it('throws when a locale has an extra unknown label key', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile(
        JSON.stringify({
          en: { key_claim: 'K', evidence_level: 'E', objective_impact: 'O', bonus: 'B' },
        }),
        (labelsPath) => {
          expect(() => loadPostLabels(labelsPath)).toThrow(/exactly the keys/);
        },
      );
    });

    it('throws when a label value is blank', async () => {
      const { loadPostLabels } = await loadAdapter();
      withLabelsFile(
        JSON.stringify({ en: { key_claim: 'K', evidence_level: '  ', objective_impact: 'O' } }),
        (labelsPath) => {
          expect(() => loadPostLabels(labelsPath)).toThrow(/non-empty string/);
        },
      );
    });
  });

  describe('assembleMarkdown: locale handling', () => {
    it('throws for an unknown locale', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_summary_en.json');
      const labels = loadPostLabels();
      expect(() => assembleMarkdown(item, 'fr', labels)).toThrow();
    });

    it('throws when item.language_code does not match the requested locale', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_summary_en.json');
      const labels = loadPostLabels();
      // Same item, different but known locale: the adapter must not render
      // one language's payload under another locale's labels.
      expect(item.language_code).toBe('en');
      expect(() => assembleMarkdown(item, 'zh', labels)).toThrow();
    });

    it('derives the adapter language set from the labels object keys, not a hardcoded array', async () => {
      const { getAdapterLanguages } = await loadAdapter();
      // Synthetic labels carry an extra locale on purpose: an implementation
      // hardcoded to ['en', 'ja', 'zh'] must fail this assertion.
      const syntheticLabels = {
        en: { key_claim: 'Key Claim', evidence_level: 'Evidence Level', objective_impact: 'Objective Impact' },
        zh: { key_claim: '關鍵主張', evidence_level: '證據等級', objective_impact: '客觀影響' },
        ja: { key_claim: '主要主張', evidence_level: '証拠レベル', objective_impact: '客観的影響' },
        fr: { key_claim: 'Affirmation clé', evidence_level: 'Niveau de preuve', objective_impact: 'Impact objectif' },
      };
      expect(getAdapterLanguages(syntheticLabels).sort()).toEqual(['en', 'fr', 'ja', 'zh']);
    });
  });

  describe('post label locale parity (src/config/post_labels.json)', () => {
    it('post_labels.json locale key set matches localeProfiles', () => {
      const labels = JSON.parse(fs.readFileSync(postLabelsPath, 'utf8'));
      expect(Object.keys(labels).sort()).toEqual(Object.keys(localeProfiles).sort());
    });

    it('every locale defines exactly the three post label keys', () => {
      const labels = JSON.parse(fs.readFileSync(postLabelsPath, 'utf8'));
      for (const locale of Object.keys(labels)) {
        expect(Object.keys(labels[locale]).sort(), locale).toEqual(
          ['evidence_level', 'key_claim', 'objective_impact'],
        );
        for (const value of Object.values(labels[locale])) {
          expect(typeof value, locale).toBe('string');
          expect((value as string).trim().length, locale).toBeGreaterThan(0);
        }
      }
    });
  });

  describe('frontmatter description', () => {
    it('takes description directly from item.summary_short, verbatim and untruncated', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_summary_en.json');
      // Fixture summary exceeds 160 chars on purpose: the removed fallback
      // cascade truncated to 160, so a verbatim match proves direct use.
      expect(item.summary_short.length).toBeGreaterThan(160);
      const labels = loadPostLabels();

      const markdown = assembleMarkdown(item, 'en', labels);

      expect(markdown).toContain(`description: ${JSON.stringify(item.summary_short)}`);
      expect(markdown).toContain(`title: ${JSON.stringify(item.display_title)}`);
      expect(markdown).toContain(`publishDate: ${JSON.stringify(item.source_published_at)}`);
      expect(markdown).toContain(`canonicalUrl: ${JSON.stringify(item.canonical_url)}`);
      expect(markdown).toContain(`disclosureNote: ${JSON.stringify(item.disclosure_note)}`);
      expect(markdown).toContain(`authorMetadata: ${JSON.stringify(item.author_metadata)}`);
    });

    it('ignores conflicting legacy summary/body fields: only summary_short is used', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_summary_en.json');
      // Simulate historical payload shapes that carried pre-spliced content or
      // per-catalog summaries. The removed fallback cascade consulted these;
      // the current adapter must not.
      item.content = 'LEGACY PRE-SPLICED BODY THAT MUST NOT LEAK';
      item.summary = 'LEGACY CATALOG SUMMARY THAT MUST NOT LEAK';
      const labels = loadPostLabels();

      const markdown = assembleMarkdown(item, 'en', labels);

      expect(markdown).not.toContain('MUST NOT LEAK');
      expect(markdown).toContain(`description: ${JSON.stringify(item.summary_short)}`);
      expect(extractBody(markdown)).toContain(item.summary_short);
    });

    it('emits canonicalUrl, disclosureNote, and authorMetadata as JSON-encoded frontmatter', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_link_en.json');
      const labels = loadPostLabels();

      const markdown = assembleMarkdown(item, 'en', labels);

      expect(markdown).toContain(`canonicalUrl: ${JSON.stringify(item.canonical_url)}`);
      expect(markdown).toContain(`disclosureNote: ${JSON.stringify(item.disclosure_note)}`);
      expect(markdown).toContain(`authorMetadata: ${JSON.stringify(item.author_metadata)}`);
    });

    it('emits explicit null/empty metadata when optional fields are absent', async () => {
      const { loadPostLabels, assembleMarkdown } = await loadAdapter();
      const item = loadFixture('item_publish_link_en.json');
      delete item.canonical_url;
      delete item.disclosure_note;
      delete item.author_metadata;
      const labels = loadPostLabels();

      const markdown = assembleMarkdown(item, 'en', labels);

      expect(markdown).toContain('canonicalUrl: null');
      expect(markdown).toContain('disclosureNote: null');
      expect(markdown).toContain('authorMetadata: {}');
    });
  });
});
