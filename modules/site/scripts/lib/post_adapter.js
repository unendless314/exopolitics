import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DEFAULT_LABELS_PATH = path.resolve(__dirname, '..', '..', 'src', 'config', 'post_labels.json');

// Fixed semantic bullet keys, in the only order the adapter renders them.
const BULLET_KEYS = ['key_claim', 'evidence_level', 'objective_impact'];

/**
 * Load the locale -> post label map. Defaults to src/config/post_labels.json,
 * the single source of truth for post label text. Throws when the file is
 * missing, unparsable, or any locale does not define exactly the three known
 * label keys with non-empty string values.
 */
export function loadPostLabels(labelsPath = DEFAULT_LABELS_PATH) {
  let raw;
  try {
    raw = fs.readFileSync(labelsPath, 'utf8');
  } catch (err) {
    throw new Error(`Failed to read post labels file at ${labelsPath}: ${err.message}`);
  }

  let labels;
  try {
    labels = JSON.parse(raw);
  } catch (err) {
    throw new Error(`Failed to parse post labels file at ${labelsPath}: ${err.message}`);
  }

  if (!labels || typeof labels !== 'object' || Array.isArray(labels)) {
    throw new Error(`Post labels file at ${labelsPath} must contain a JSON object keyed by locale.`);
  }

  const locales = Object.keys(labels);
  if (locales.length === 0) {
    throw new Error(`Post labels file at ${labelsPath} defines no locales.`);
  }

  for (const locale of locales) {
    const entry = labels[locale];
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`Post labels for locale "${locale}" must be an object with the three label keys.`);
    }
    const keys = Object.keys(entry);
    if (keys.length !== BULLET_KEYS.length || !BULLET_KEYS.every((key) => keys.includes(key))) {
      throw new Error(
        `Post labels for locale "${locale}" must define exactly the keys: ${BULLET_KEYS.join(', ')}.`,
      );
    }
    for (const key of BULLET_KEYS) {
      if (typeof entry[key] !== 'string' || entry[key].trim().length === 0) {
        throw new Error(`Post label "${key}" for locale "${locale}" must be a non-empty string.`);
      }
    }
  }

  return labels;
}

/**
 * Hard-fail validation for a publish_export item JSON payload. Throws unless:
 * - slug and display_title are non-empty strings,
 * - source_published_at parses as a date,
 * - summary_short is a non-empty string after trimming,
 * - bullets is either null or an object with exactly the three known keys
 *   whose values are all non-empty strings.
 */
export function validateItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    throw new Error('Item must be a JSON object.');
  }

  if (typeof item.slug !== 'string' || item.slug.trim().length === 0) {
    throw new Error('Item is missing a valid slug.');
  }
  if (typeof item.display_title !== 'string' || item.display_title.trim().length === 0) {
    throw new Error(`Item ${item.slug} is missing a valid display_title.`);
  }
  if (!item.source_published_at || isNaN(Date.parse(item.source_published_at))) {
    throw new Error(`Item ${item.slug} has missing or invalid source_published_at.`);
  }
  if (typeof item.summary_short !== 'string' || item.summary_short.trim().length === 0) {
    throw new Error(`Item ${item.slug} is missing a valid summary_short.`);
  }

  if (item.bullets === null) {
    return;
  }
  if (typeof item.bullets !== 'object' || Array.isArray(item.bullets)) {
    throw new Error(`Item ${item.slug} bullets must be null or an object with the three known keys.`);
  }
  const keys = Object.keys(item.bullets);
  if (keys.length !== BULLET_KEYS.length || !BULLET_KEYS.every((key) => keys.includes(key))) {
    throw new Error(
      `Item ${item.slug} bullets must contain exactly the keys: ${BULLET_KEYS.join(', ')}.`,
    );
  }
  for (const key of BULLET_KEYS) {
    if (typeof item.bullets[key] !== 'string' || item.bullets[key].trim().length === 0) {
      throw new Error(`Item ${item.slug} bullet "${key}" must be a non-empty string.`);
    }
  }
}

/**
 * Assemble the generated Markdown document for one item under one locale:
 * YAML frontmatter plus a body of the summary paragraph and, when bullets
 * are present, a `* **<locale label>**: <value>` list in fixed key order.
 * Hard-fails on invalid items, unknown locales, and item.language_code not
 * matching the requested locale.
 */
export function assembleMarkdown(item, locale, labels) {
  validateItem(item);

  if (!labels || typeof labels !== 'object' || !Object.prototype.hasOwnProperty.call(labels, locale)) {
    throw new Error(`Unknown locale "${locale}" for item ${item.slug}.`);
  }
  if (item.language_code !== locale) {
    throw new Error(
      `Item ${item.slug} language_code "${item.language_code}" does not match requested locale "${locale}".`,
    );
  }

  let body = item.summary_short;
  if (item.bullets !== null) {
    const bulletLines = BULLET_KEYS.map((key) => `* **${labels[locale][key]}**: ${item.bullets[key]}`);
    body = `${item.summary_short}\n\n${bulletLines.join('\n')}`;
  }

  return [
    '---',
    `title: ${JSON.stringify(item.display_title)}`,
    `publishDate: ${JSON.stringify(item.source_published_at)}`,
    `description: ${JSON.stringify(item.summary_short)}`,
    `canonicalUrl: ${JSON.stringify(item.canonical_url || null)}`,
    `disclosureNote: ${JSON.stringify(item.disclosure_note || null)}`,
    `authorMetadata: ${JSON.stringify(item.author_metadata || {})}`,
    '---',
    '',
    body,
  ].join('\n');
}

/**
 * The adapter language set, derived from the loaded labels object keys.
 * Replaces the hardcoded ['en', 'ja', 'zh'] array in generate-posts.js.
 */
export function getAdapterLanguages(labels) {
  return Object.keys(labels);
}
