import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPostLabels, assembleMarkdown, getAdapterLanguages } from './lib/post_adapter.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const workspaceRoot = path.resolve(__dirname, '..', '..', '..');

// Paths
const exportBaseDir = path.join(workspaceRoot, 'data', 'publish_export');
const generatedDir = path.resolve(__dirname, '..', 'src', 'content', 'posts', 'generated');

console.log(`Starting post generation...`);
console.log(`Reading from: ${exportBaseDir}`);
console.log(`Writing to: ${generatedDir}`);

// 1. Cleanup and recreate generated directory
if (fs.existsSync(generatedDir)) {
  console.log(`Cleaning up existing generated directory...`);
  fs.rmSync(generatedDir, { recursive: true, force: true });
}
fs.mkdirSync(generatedDir, { recursive: true });

// 2. Load locale post labels and derive the language set from them
const labels = loadPostLabels();
const languages = getAdapterLanguages(labels);
const translationMap = {};

for (const lang of languages) {
  const langDir = path.join(exportBaseDir, lang);
  if (!fs.existsSync(langDir)) {
    console.warn(`Warning: Language directory ${langDir} does not exist. Skipping.`);
    continue;
  }

  console.log(`Processing language: ${lang}`);

  // Target directory for generated markdown
  const targetLangDir = path.join(generatedDir, lang);
  fs.mkdirSync(targetLangDir, { recursive: true });

  // Process items folder
  const itemsDir = path.join(langDir, 'items');
  if (!fs.existsSync(itemsDir)) {
    console.warn(`Warning: Items directory ${itemsDir} does not exist for language ${lang}.`);
    continue;
  }

  const itemFiles = fs.readdirSync(itemsDir).filter(file => file.endsWith('.json'));
  console.log(`Found ${itemFiles.length} items to process for ${lang}`);

  for (const file of itemFiles) {
    const itemPath = path.join(itemsDir, file);
    let item;
    try {
      item = JSON.parse(fs.readFileSync(itemPath, 'utf8'));
    } catch (err) {
      console.error(`Error reading or parsing item ${file} in ${lang}:`, err);
      process.exit(1);
    }

    // Validation and Markdown assembly (hard failure on invalid items)
    let markdown;
    try {
      markdown = assembleMarkdown(item, lang, labels);
    } catch (err) {
      console.error(`Error: Item ${file} in ${lang} failed validation: ${err.message}`);
      process.exit(1);
    }

    // Write file
    const targetFile = path.join(targetLangDir, `${item.slug}.md`);
    fs.writeFileSync(targetFile, markdown, 'utf8');

    // Add to translation map
    if (!translationMap[item.slug]) {
      translationMap[item.slug] = [];
    }
    translationMap[item.slug].push(lang);
  }
}

// Write translation map
const translationMapFile = path.resolve(__dirname, '..', 'src', 'content', '_translation_map.json');
fs.writeFileSync(translationMapFile, JSON.stringify(translationMap, null, 2), 'utf8');
console.log(`Translation map written to: ${translationMapFile}`);

console.log(`Post generation completed successfully!`);
