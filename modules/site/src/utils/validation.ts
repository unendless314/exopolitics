export interface CatalogItem {
  slug: string;
  display_title: string;
  /** Required handoff field; there is no display_title fallback. */
  summary_short: string;
  canonical_url?: string | null;
  source_published_at: string;
  approved_at?: string;
  published_at?: string;
}

export interface ArchiveManifestEntry {
  archive_month: string;
  file_name: string;
  item_count: number;
  updated_at: string;
}

export interface PublishStats {
  total_active_published_items_by_language: Record<string, number>;
  total_withdrawn_items_by_language: Record<string, number>;
  latest_index_count_by_language: Record<string, number>;
  archive_month_count_by_language: Record<string, number>;
  oldest_archive_month_by_language: Record<string, string | null>;
  last_export_run_timestamp: string;
}

const STATS_COUNT_MAPS = [
  'total_active_published_items_by_language',
  'total_withdrawn_items_by_language',
  'latest_index_count_by_language',
  'archive_month_count_by_language',
] as const;

const ARCHIVE_MONTH_PATTERN = /^\d{4}-\d{2}$/;

// Handoff slug contract (docs/DATA_HANDOFF_CONTRACT.md section 1.6) applies to
// every slug field, catalog entries included: an unsafe slug must never reach
// Timeline URL construction.
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

// Archive manifest file_name is joined into a filesystem path by
// loadArchiveMonth; it must be a plain JSON file basename, never something
// that can escape the locale archives directory.
const SAFE_ARCHIVE_FILE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*\.json$/;

/** True when a file name cannot escape its parent directory. */
export function isSafeArchiveFileName(name: unknown): name is string {
  return (
    typeof name === 'string' &&
    SAFE_ARCHIVE_FILE_PATTERN.test(name) &&
    !name.includes('..')
  );
}

function fail(message: string): never {
  throw new Error(`[Data Integrity Validation Failed] ${message}`);
}

function isPlainObject(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireNonBlankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function requireParseableDate(value: unknown): value is string {
  return typeof value === 'string' && !isNaN(Date.parse(value));
}

/**
 * Validates a raw item from publish_export catalog JSON (e.g. index.json or archive monthly JSON)
 * and returns a validated CatalogItem. Throws an error if validation fails.
 */
export function validateCatalogItem(item: any, sourceName: string): CatalogItem {
  if (!isPlainObject(item)) {
    fail(`Expected object in ${sourceName}, got: ${JSON.stringify(item)}`);
  }
  if (!requireNonBlankString(item.slug)) {
    fail(`Item in ${sourceName} is missing a valid 'slug': ${JSON.stringify(item)}`);
  }
  if (!SLUG_PATTERN.test(item.slug)) {
    fail(`Item slug '${item.slug}' in ${sourceName} violates the handoff slug format (^[a-z0-9][a-z0-9-]*$)`);
  }
  if (!requireNonBlankString(item.display_title)) {
    fail(`Item '${item.slug}' in ${sourceName} is missing a valid 'display_title'`);
  }
  if (!requireNonBlankString(item.summary_short)) {
    fail(`Item '${item.slug}' in ${sourceName} is missing a valid 'summary_short'`);
  }
  if (!requireParseableDate(item.source_published_at)) {
    fail(`Item '${item.slug}' in ${sourceName} has missing or invalid 'source_published_at'`);
  }
  return {
    slug: item.slug,
    display_title: item.display_title,
    summary_short: item.summary_short,
    canonical_url: item.canonical_url,
    source_published_at: item.source_published_at,
    approved_at: item.approved_at,
    published_at: item.published_at,
  };
}

/** Validates that parsed catalog JSON is an array of valid catalog items. */
export function validateCatalogArray(data: unknown, sourceName: string): CatalogItem[] {
  if (!Array.isArray(data)) {
    fail(`Expected an array in ${sourceName}, got: ${Array.isArray(data) ? 'array' : typeof data}`);
  }
  return data.map((item, index) => validateCatalogItem(item, `${sourceName} [index ${index}]`));
}

/** Validates one entry of a monthly archive manifest (archives/index.json). */
export function validateArchiveManifestEntry(entry: any, sourceName: string): ArchiveManifestEntry {
  if (!isPlainObject(entry)) {
    fail(`Expected object in ${sourceName}, got: ${JSON.stringify(entry)}`);
  }
  if (!requireNonBlankString(entry.archive_month) || !ARCHIVE_MONTH_PATTERN.test(entry.archive_month)) {
    fail(`Entry in ${sourceName} is missing a valid 'archive_month' (YYYY-MM): ${JSON.stringify(entry)}`);
  }
  if (!isSafeArchiveFileName(entry.file_name)) {
    fail(`Entry '${entry.archive_month}' in ${sourceName} is missing a valid 'file_name' (plain .json basename): ${JSON.stringify(entry.file_name)}`);
  }
  if (!Number.isInteger(entry.item_count) || entry.item_count < 0) {
    fail(`Entry '${entry.archive_month}' in ${sourceName} has missing or invalid 'item_count' (non-negative integer)`);
  }
  if (!requireParseableDate(entry.updated_at)) {
    fail(`Entry '${entry.archive_month}' in ${sourceName} has missing or invalid 'updated_at'`);
  }
  return {
    archive_month: entry.archive_month,
    file_name: entry.file_name,
    item_count: entry.item_count,
    updated_at: entry.updated_at,
  };
}

/** Validates that parsed archive manifest JSON is an array of valid entries. */
export function validateArchiveManifest(data: unknown, sourceName: string): ArchiveManifestEntry[] {
  if (!Array.isArray(data)) {
    fail(`Expected an array in ${sourceName}, got: ${Array.isArray(data) ? 'array' : typeof data}`);
  }
  return data.map((entry, index) => validateArchiveManifestEntry(entry, `${sourceName} [index ${index}]`));
}

/**
 * Validates the global stats.json payload. The required locale set is derived
 * from localeProfiles by the caller — never from the stats page's table
 * columns. Every required locale must appear in all five per-locale maps;
 * extra, unknown locale keys from upstream are tolerated so a newly added
 * language cannot block the site before the site itself is configured.
 */
export function validateStats(raw: any, requiredLocales: readonly string[]): PublishStats {
  if (!isPlainObject(raw)) {
    fail(`Expected stats.json to contain a JSON object, got: ${JSON.stringify(raw)}`);
  }
  if (!requireParseableDate(raw.last_export_run_timestamp)) {
    fail(`stats.json has missing or invalid 'last_export_run_timestamp'`);
  }

  for (const mapName of STATS_COUNT_MAPS) {
    const map = raw[mapName];
    if (!isPlainObject(map)) {
      fail(`stats.json is missing the '${mapName}' map`);
    }
    for (const locale of requiredLocales) {
      const value = map[locale];
      if (!Number.isInteger(value) || (value as number) < 0) {
        fail(
          `stats.json map '${mapName}' must define locale '${locale}' as a non-negative integer, got: ${JSON.stringify(value)}`,
        );
      }
    }
  }

  const oldestMap = raw.oldest_archive_month_by_language;
  if (!isPlainObject(oldestMap)) {
    fail(`stats.json is missing the 'oldest_archive_month_by_language' map`);
  }
  for (const locale of requiredLocales) {
    const value = oldestMap[locale];
    // `null` is tolerated until the publish owner confirms how a locale with
    // no archives should be represented.
    if (value !== null && !(typeof value === 'string' && ARCHIVE_MONTH_PATTERN.test(value))) {
      fail(
        `stats.json map 'oldest_archive_month_by_language' must define locale '${locale}' as 'YYYY-MM' or null, got: ${JSON.stringify(value)}`,
      );
    }
  }

  return raw as PublishStats;
}
