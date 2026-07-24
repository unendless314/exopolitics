import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { localeProfiles } from '../src/utils/i18n';

/**
 * Phase 1 contract tests for the site adapter rework
 * (TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md, plan section 7.1 "site adapter" row).
 *
 * These tests target the Phase 4 adapter API, which does not exist yet:
 *
 *   Module: modules/site/scripts/lib/post_adapter.js (plain Node ESM, no deps)
 *   - loadPostLabels(labelsPath?) => { [locale]: { key_claim, evidence_level, objective_impact } }
 *       Reads src/config/post_labels.json by default. Throws on missing/invalid file.
 *   - validateItem(item) => void
 *       Throws unless: slug/display_title are non-empty strings,
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
 *
 * Every test that touches the adapter module or src/config/post_labels.json
 * is EXPECTED TO FAIL until Phase 4 lands. They must fail because the target
 * module/data file is missing, not because of test-code errors.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fixturesDir = path.join(__dirname, 'fixtures');
const postLabelsPath = path.resolve(__dirname, '..', 'src', 'config', 'post_labels.json');

const ADAPTER_MODULE = '../scripts/lib/post_adapter.js';

const EXPECTED_LABELS: Record<string, { key_claim: string; evidence_level: string; objective_impact: string }> = {
  en: { key_claim: 'Key Claim', evidence_level: 'Evidence Level', objective_impact: 'Objective Impact' },
  zh: { key_claim: '關鍵主張', evidence_level: '證據等級', objective_impact: '客觀影響' },
  ja: { key_claim: '主要主張', evidence_level: '証拠レベル', objective_impact: '客観的影響' },
};

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

describe('generate-posts adapter contract (Phase 4 target)', () => {
  describe('assembleMarkdown: localized post labels', () => {
    for (const locale of ['en', 'zh', 'ja'] as const) {
      it(`assembles ${locale} markdown with ${locale} labels`, async () => {
        const { loadPostLabels, assembleMarkdown } = await loadAdapter();
        const item = loadFixture(`item_publish_summary_${locale}.json`);
        const labels = loadPostLabels();
        const expected = EXPECTED_LABELS[locale];

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
      // Points at the real path; the file is created in Phase 4, so this
      // fails with ENOENT until then — that failure is the contract.
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

    it('adapter API takes no summaryMap parameter (summaryMap removal is structural)', async () => {
      const { validateItem, assembleMarkdown } = await loadAdapter();
      expect(validateItem.length).toBe(1);
      expect(assembleMarkdown.length).toBe(3);
    });
  });
});
