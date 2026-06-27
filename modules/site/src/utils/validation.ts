export interface CatalogItem {
  slug: string;
  display_title: string;
  summary_short?: string;
  canonical_url?: string | null;
  source_published_at: string;
  approved_at?: string;
  published_at?: string;
}

/**
 * Validates a raw item from publish_export catalog JSON (e.g. index.json or archive monthly JSON)
 * and returns a validated CatalogItem. Throws an error if validation fails.
 */
export function validateCatalogItem(item: any, sourceName: string): CatalogItem {
  if (!item || typeof item !== "object") {
    throw new Error(`[Data Integrity Validation Failed] Expected object in ${sourceName}, got: ${JSON.stringify(item)}`);
  }
  if (!item.slug || typeof item.slug !== "string") {
    throw new Error(`[Data Integrity Validation Failed] Item in ${sourceName} is missing a valid 'slug': ${JSON.stringify(item)}`);
  }
  if (!item.display_title || typeof item.display_title !== "string") {
    throw new Error(`[Data Integrity Validation Failed] Item '${item.slug}' in ${sourceName} is missing a valid 'display_title'`);
  }
  if (!item.source_published_at || isNaN(Date.parse(item.source_published_at))) {
    throw new Error(`[Data Integrity Validation Failed] Item '${item.slug}' in ${sourceName} has missing or invalid 'source_published_at'`);
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
