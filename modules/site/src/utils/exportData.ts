import fs from 'node:fs';
import path from 'node:path';

import {
  validateCatalogArray,
  validateArchiveManifest,
  validateStats,
  isSafeArchiveFileName,
} from './validation';
import type {
  CatalogItem,
  ArchiveManifestEntry,
  PublishStats,
} from './validation';

/**
 * Shared loaders for publish_export JSON inputs. Every page/component and the
 * Footer must go through these instead of ad-hoc fs reads, so missing files,
 * JSON parse failures, root type errors, and schema violations all surface as
 * one consistent hard-fail error channel.
 *
 * None of these functions degrade to empty data: a missing expected export
 * file is always an export completeness failure.
 */

function fail(message: string): never {
  throw new Error(`[Data Integrity Validation Failed] ${message}`);
}

function readJsonFile(filePath: string, description: string): unknown {
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, 'utf8');
  } catch (err) {
    fail(`Missing or unreadable ${description} at ${filePath}: ${err instanceof Error ? err.message : String(err)}`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    fail(`Failed to parse ${description} at ${filePath}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** Load and validate `data/publish_export/<lang>/index.json`. */
export function loadLanguageIndex(exportRoot: string, lang: string): CatalogItem[] {
  const filePath = path.join(exportRoot, lang, 'index.json');
  return validateCatalogArray(readJsonFile(filePath, `language index (${lang})`), `index.json (${lang})`);
}

/** Load and validate `data/publish_export/<lang>/archives/index.json`. */
export function loadArchiveManifest(exportRoot: string, lang: string): ArchiveManifestEntry[] {
  const filePath = path.join(exportRoot, lang, 'archives', 'index.json');
  return validateArchiveManifest(
    readJsonFile(filePath, `archive manifest (${lang})`),
    `archive manifest (${lang})`,
  );
}

/** Load and validate `data/publish_export/<lang>/archives/<fileName>`. */
export function loadArchiveMonth(exportRoot: string, lang: string, fileName: string): CatalogItem[] {
  // Defense at the filesystem boundary even for callers that bypass manifest
  // validation: the file name must not be able to leave the archives directory.
  if (!isSafeArchiveFileName(fileName)) {
    fail(`Unsafe archive file name for locale '${lang}': ${JSON.stringify(fileName)}`);
  }
  const filePath = path.join(exportRoot, lang, 'archives', fileName);
  return validateCatalogArray(
    readJsonFile(filePath, `monthly archive ${fileName} (${lang})`),
    `archive file ${fileName} (${lang})`,
  );
}

/**
 * Load and validate `data/publish_export/stats.json`. requiredLocales must be
 * derived from localeProfiles by the caller.
 */
export function loadStats(exportRoot: string, requiredLocales: readonly string[]): PublishStats {
  const filePath = path.join(exportRoot, 'stats.json');
  return validateStats(readJsonFile(filePath, 'global stats'), requiredLocales);
}
