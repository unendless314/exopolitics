import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Absolute path to the monorepo workspace root. */
export const workspaceRoot = path.resolve(__dirname, '..', '..', '..', '..');

/** Environment variable that overrides the publish export root. */
export const EXPORT_ROOT_ENV_VAR = 'SITE_PUBLISH_EXPORT_DIR';

// Pointer contract, identical to the publish side
// (modules/publish/src/generation_store.py): a strict Windows-safe
// generation id, calendar-valid ISO-8601 UTC second-precision timestamps, a
// non-empty language list, and the versioned content-fingerprint format.
// The generation id is validated before it is ever joined into a path, so
// no arbitrary string reaches the filesystem.
const GENERATION_ID_RE = /^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$/;
const ISO_TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const FINGERPRINT_RE = /^sha256-exportstate-v1:[0-9a-f]{64}$/;

function isValidIsoTimestamp(value) {
  if (typeof value !== 'string' || !ISO_TIMESTAMP_RE.test(value)) {
    return false;
  }
  // The regex pins the shape; the Date round-trip rejects calendar-impossible
  // values (e.g. February 30) that the shape admits and that JavaScript would
  // otherwise silently roll over into a different day.
  const parsed = new Date(value);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString() === `${value.slice(0, -1)}.000Z`;
}

function fail(message) {
  throw new Error(`[Data Integrity Validation Failed] ${message}`);
}

/**
 * Base export-root resolution policy shared by the generator script, the
 * Astro loaders, and the Footer stats loader.
 *
 * - When SITE_PUBLISH_EXPORT_DIR is not set (or blank), returns the workspace
 *   default `<workspace>/data/publish_export`. Existence is not checked here;
 *   a missing default-root pointer hard-fails later in resolveExportContext.
 * - When set, resolves the value to an absolute path (relative values resolve
 *   against process.cwd()) and throws a clear error if the directory does not
 *   exist. An explicit override must never silently fall back to the default.
 */
export function resolveExportBase(env = process.env) {
  const override = env[EXPORT_ROOT_ENV_VAR];
  if (override && override.trim().length > 0) {
    const resolved = path.resolve(override.trim());
    let stat;
    try {
      stat = fs.statSync(resolved);
    } catch (err) {
      throw new Error(
        `${EXPORT_ROOT_ENV_VAR} points to "${resolved}", which does not exist: ${err.message}`,
      );
    }
    if (!stat.isDirectory()) {
      throw new Error(`${EXPORT_ROOT_ENV_VAR} points to "${resolved}", which is not a directory.`);
    }
    return resolved;
  }
  return path.join(workspaceRoot, 'data', 'publish_export');
}

/**
 * Fail-stop validation of a parsed current.json, mirroring the publish-side
 * contract: required fields, strict generation id format, calendar-valid
 * timestamps, a non-empty language list, and the referenced generation
 * directory must exist and be a directory.
 */
function validatePointer(pointer, base, pointerPath) {
  if (pointer === null || typeof pointer !== 'object' || Array.isArray(pointer)) {
    fail(`current.json at ${pointerPath} is invalid: top-level value must be an object.`);
  }
  const { generation, export_completed_at, last_successful_run_at, languages, content_fingerprint } =
    pointer;
  if (typeof generation !== 'string' || !GENERATION_ID_RE.test(generation)) {
    fail(
      `current.json at ${pointerPath} is invalid: 'generation' must match ` +
        `${GENERATION_ID_RE.source}, got ${JSON.stringify(generation)}.`,
    );
  }
  for (const [field, value] of [
    ['export_completed_at', export_completed_at],
    ['last_successful_run_at', last_successful_run_at],
  ]) {
    if (!isValidIsoTimestamp(value)) {
      fail(
        `current.json at ${pointerPath} is invalid: '${field}' must be a calendar-valid ` +
          `ISO-8601 UTC timestamp, got ${JSON.stringify(value)}.`,
      );
    }
  }
  if (!Array.isArray(languages) || languages.length === 0 || !languages.every((lang) => typeof lang === 'string')) {
    fail(`current.json at ${pointerPath} is invalid: 'languages' must be a non-empty list of strings.`);
  }
  if (typeof content_fingerprint !== 'string' || !FINGERPRINT_RE.test(content_fingerprint)) {
    fail(
      `current.json at ${pointerPath} is invalid: 'content_fingerprint' must be a ` +
        `sha256-exportstate-v1 digest string.`,
    );
  }
  const generationRoot = path.join(base, 'generations', generation);
  let stat;
  try {
    stat = fs.statSync(generationRoot);
  } catch {
    stat = null;
  }
  if (!stat || !stat.isDirectory()) {
    fail(
      `current.json at ${pointerPath} points at generation '${generation}', but ` +
        `${generationRoot} does not exist or is not a directory.`,
    );
  }
  return generationRoot;
}

/**
 * Resolve the live export generation through the atomic pointer
 * (Phase B1: <base>/current.json -> <base>/generations/<generation>).
 *
 * Returns `{ generationRoot, pointer }` where `pointer` is the validated
 * current.json payload. A missing, unparseable or invalid pointer — or a
 * pointer whose generation directory is absent — hard-fails: readers never
 * silently fall back to a stale or flat layout.
 */
export function resolveExportContext(env = process.env) {
  const base = resolveExportBase(env);
  const pointerPath = path.join(base, 'current.json');
  let raw;
  try {
    raw = fs.readFileSync(pointerPath, 'utf8');
  } catch (err) {
    fail(`Missing or unreadable publish export pointer at ${pointerPath}: ${err.message}`);
  }
  let pointer;
  try {
    pointer = JSON.parse(raw);
  } catch (err) {
    fail(`Failed to parse publish export pointer at ${pointerPath}: ${err.message}`);
  }
  const generationRoot = validatePointer(pointer, base, pointerPath);
  return { generationRoot, pointer };
}

/**
 * Absolute path to the live export generation directory, resolved through
 * the current.json pointer. All artifact readers (index, archives, items,
 * stats) operate on this root.
 */
export function resolveExportRoot(env = process.env) {
  return resolveExportContext(env).generationRoot;
}
