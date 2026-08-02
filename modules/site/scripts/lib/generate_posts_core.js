import fs from 'node:fs';
import path from 'node:path';

import { loadPostLabels, assembleMarkdown, getAdapterLanguages } from './post_adapter.js';

/**
 * Testable core of the generate-posts CLI.
 *
 * Reads detail item JSONs from <exportBaseDir>/<lang>/items/, assembles
 * Markdown per locale, and produces the translation map. All outputs are
 * written into a staging directory first and only promoted to the real
 * artifact locations once every input validated and every write succeeded —
 * a failed run never leaves partial Markdown, a partial map, or a stale
 * success signal behind.
 *
 * Export completeness is enforced: a configured locale missing its language
 * directory or items directory is an error, never a warning-and-skip.
 *
 * @param {object} options
 * @param {string} options.exportBaseDir - publish_export root directory.
 * @param {string} options.generatedDir - final Markdown output directory.
 * @param {string} options.translationMapFile - final translation map JSON path.
 * @param {string} [options.labelsPath] - post labels config path (adapter default when omitted).
 * @param {{ log: (msg: string) => void }} [options.logger] - log sink; defaults to console.
 * @returns {{ languages: string[], itemCount: number }} summary of the run.
 * @throws {Error} on any completeness, parse, validation, or duplicate-slug failure.
 */
export function generatePosts({
  exportBaseDir,
  generatedDir,
  translationMapFile,
  labelsPath,
  logger = console,
}) {
  const stagingDir = `${generatedDir}.staging-${process.pid}`;
  const stagingMapFile = `${translationMapFile}.staging-${process.pid}`;

  const cleanupStaging = () => {
    fs.rmSync(stagingDir, { recursive: true, force: true });
    fs.rmSync(stagingMapFile, { force: true });
  };

  cleanupStaging();
  fs.mkdirSync(stagingDir, { recursive: true });

  try {
    const labels = loadPostLabels(labelsPath);
    const languages = getAdapterLanguages(labels);
    // Null-prototype map: prototype-named but valid slugs (e.g. "constructor",
    // "__proto__") must behave like any other slug.
    const translationMap = Object.create(null);
    let itemCount = 0;

    for (const lang of languages) {
      const langDir = path.join(exportBaseDir, lang);
      if (!fs.existsSync(langDir)) {
        throw new Error(
          `Export completeness failure: language directory ${langDir} does not exist for configured locale "${lang}".`,
        );
      }

      const itemsDir = path.join(langDir, 'items');
      if (!fs.existsSync(itemsDir)) {
        throw new Error(
          `Export completeness failure: items directory ${itemsDir} does not exist for locale "${lang}".`,
        );
      }

      logger.log(`Processing language: ${lang}`);
      const targetLangDir = path.join(stagingDir, lang);
      fs.mkdirSync(targetLangDir, { recursive: true });

      const itemFiles = fs
        .readdirSync(itemsDir)
        .filter((file) => file.endsWith('.json'))
        .sort();
      logger.log(`Found ${itemFiles.length} items to process for ${lang}`);

      const seenSlugs = new Set();
      for (const file of itemFiles) {
        const itemPath = path.join(itemsDir, file);
        let item;
        try {
          item = JSON.parse(fs.readFileSync(itemPath, 'utf8'));
        } catch (err) {
          throw new Error(`Error reading or parsing item ${file} in ${lang}: ${err.message}`);
        }

        let markdown;
        try {
          markdown = assembleMarkdown(item, lang, labels);
        } catch (err) {
          throw new Error(`Item ${file} in ${lang} failed validation: ${err.message}`);
        }

        if (seenSlugs.has(item.slug)) {
          throw new Error(
            `Duplicate slug "${item.slug}" in locale "${lang}": refusing to overwrite generated Markdown or duplicate the translation map entry.`,
          );
        }
        seenSlugs.add(item.slug);

        fs.writeFileSync(path.join(targetLangDir, `${item.slug}.md`), markdown, 'utf8');
        itemCount += 1;

        if (!translationMap[item.slug]) {
          translationMap[item.slug] = [];
        }
        translationMap[item.slug].push(lang);
      }
    }

    // Deterministic artifact contract: locale lists are sorted (independent of
    // the labels config key order), and top-level slug keys are sorted too.
    const sortedMap = Object.create(null);
    for (const slug of Object.keys(translationMap).sort()) {
      sortedMap[slug] = [...translationMap[slug]].sort();
    }

    fs.mkdirSync(path.dirname(translationMapFile), { recursive: true });
    fs.writeFileSync(stagingMapFile, JSON.stringify(sortedMap, null, 2), 'utf8');

    // Promotion as a transaction over the two live artifacts (generated
    // directory + translation map). A two-path pair cannot be committed
    // atomically by one filesystem operation, so this guarantees instead:
    // - pre-commit housekeeping failure aborts before any live artifact changes,
    // - a mid-promotion failure restores the previous pair in reverse order,
    //   and a failing rollback surfaces both errors and retains the backups
    //   for manual recovery (never silently suppressed),
    // - after commit, backup deletion is housekeeping only: it logs a warning
    //   and never pushes the new live pair into rollback.
    const backupDir = `${generatedDir}.backup-${process.pid}`;
    const backupMapFile = `${translationMapFile}.backup-${process.pid}`;

    // Pre-commit: clear stale backups. Failure here means nothing live has
    // changed yet, so it is safe to abort outright.
    fs.rmSync(backupDir, { recursive: true, force: true });
    fs.rmSync(backupMapFile, { force: true });

    let mapBackedUp = false;
    let mapPromoted = false;
    let dirBackedUp = false;
    let dirPromoted = false;

    try {
      if (fs.existsSync(translationMapFile)) {
        fs.renameSync(translationMapFile, backupMapFile);
        mapBackedUp = true;
      }
      fs.renameSync(stagingMapFile, translationMapFile);
      mapPromoted = true;

      if (fs.existsSync(generatedDir)) {
        fs.renameSync(generatedDir, backupDir);
        dirBackedUp = true;
      }
      fs.mkdirSync(path.dirname(generatedDir), { recursive: true });
      fs.renameSync(stagingDir, generatedDir);
      dirPromoted = true;
    } catch (promotionErr) {
      // Restore in reverse order, best effort per step, collecting failures.
      const rollbackErrors = [];
      if (dirPromoted) {
        try {
          fs.rmSync(generatedDir, { recursive: true, force: true });
        } catch (err) {
          rollbackErrors.push(err);
        }
      }
      if (dirBackedUp && !fs.existsSync(generatedDir) && fs.existsSync(backupDir)) {
        try {
          fs.renameSync(backupDir, generatedDir);
        } catch (err) {
          rollbackErrors.push(err);
        }
      }
      if (mapPromoted) {
        try {
          fs.rmSync(translationMapFile, { force: true });
        } catch (err) {
          rollbackErrors.push(err);
        }
      }
      if (mapBackedUp && !fs.existsSync(translationMapFile) && fs.existsSync(backupMapFile)) {
        try {
          fs.renameSync(backupMapFile, translationMapFile);
        } catch (err) {
          rollbackErrors.push(err);
        }
      }
      if (rollbackErrors.length > 0) {
        const details = rollbackErrors
          .map((err) => (err instanceof Error ? err.message : String(err)))
          .join('; ');
        throw new Error(
          `Post generation promotion failed: ${promotionErr.message}. ` +
            `Rollback also failed: ${details}. ` +
            `Backups retained at ${backupDir} and ${backupMapFile} for manual recovery.`,
        );
      }
      throw promotionErr;
    }

    // Committed: both new artifacts are live. Backup deletion is housekeeping,
    // not part of the transaction — warn and leave the backup for later.
    for (const stalePath of [backupDir, backupMapFile]) {
      if (fs.existsSync(stalePath)) {
        try {
          fs.rmSync(stalePath, { recursive: true, force: true });
        } catch (err) {
          logger.log(
            `Warning: could not remove backup ${stalePath}; leaving it for later cleanup. ` +
              `${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    }

    logger.log(`Translation map written to: ${translationMapFile}`);
    logger.log(`Post generation completed successfully! (${itemCount} items)`);
    return { languages, itemCount };
  } catch (err) {
    cleanupStaging();
    throw err;
  }
}
