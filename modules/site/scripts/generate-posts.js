import fs from 'node:fs';
import path from 'node:path';

// Paths
const exportBaseDir = path.resolve('../../data/publish_export');
const generatedDir = path.resolve('src/content/posts/generated');

console.log(`Starting post generation...`);
console.log(`Reading from: ${exportBaseDir}`);
console.log(`Writing to: ${generatedDir}`);

// 1. Cleanup and recreate generated directory
if (fs.existsSync(generatedDir)) {
  console.log(`Cleaning up existing generated directory...`);
  fs.rmSync(generatedDir, { recursive: true, force: true });
}
fs.mkdirSync(generatedDir, { recursive: true });

// 2. Identify languages
const languages = ['en', 'ja', 'zh'];

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

  // Map to store slug -> summary_short
  const summaryMap = new Map();

  // Load index.json
  const indexFile = path.join(langDir, 'index.json');
  if (fs.existsSync(indexFile)) {
    try {
      const items = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
      for (const item of items) {
        if (item.slug && item.summary_short) {
          summaryMap.set(item.slug, item.summary_short);
        }
      }
    } catch (err) {
      console.error(`Error parsing index.json for ${lang}:`, err);
      process.exit(1);
    }
  }

  // Load monthly archives
  const archivesIndexFile = path.join(langDir, 'archives', 'index.json');
  if (fs.existsSync(archivesIndexFile)) {
    try {
      const archivesIndex = JSON.parse(fs.readFileSync(archivesIndexFile, 'utf8'));
      for (const archive of archivesIndex) {
        const archiveFile = path.join(langDir, 'archives', archive.file_name);
        if (fs.existsSync(archiveFile)) {
          const archiveItems = JSON.parse(fs.readFileSync(archiveFile, 'utf8'));
          for (const item of archiveItems) {
            if (item.slug && item.summary_short) {
              summaryMap.set(item.slug, item.summary_short);
            }
          }
        }
      }
    } catch (err) {
      console.error(`Error parsing archives for ${lang}:`, err);
      process.exit(1);
    }
  }

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

    // Validation
    if (!item.slug || !item.display_title) {
      console.error(`Error: Item ${file} in ${lang} is missing slug or display_title.`);
      process.exit(1);
    }
    if (!item.source_published_at || isNaN(Date.parse(item.source_published_at))) {
      console.error(`Error: Item ${file} in ${lang} has missing or invalid source_published_at.`);
      process.exit(1);
    }

    const {
      slug,
      display_title,
      content,
      canonical_url,
      source_published_at,
      disclosure_note,
      author_metadata
    } = item;

    // Determine SEO description
    let description = '';
    // 1. Primary: summary_short from maps
    if (summaryMap.has(slug)) {
      description = summaryMap.get(slug);
    }
    // 2. Secondary: Parse first paragraph of content, strip MD, truncate to 160 chars
    if (!description && content) {
      const firstParagraph = getFirstParagraph(content);
      const cleanParagraph = stripMarkdown(firstParagraph);
      description = truncateDescription(cleanParagraph);
    }
    // 3. Tertiary: Fallback to display_title
    if (!description) {
      description = display_title;
    }

    // Create YAML frontmatter
    const frontmatter = [
      '---',
      `title: ${JSON.stringify(display_title)}`,
      `publishDate: ${JSON.stringify(source_published_at)}`,
      `description: ${JSON.stringify(description)}`,
      `canonicalUrl: ${JSON.stringify(canonical_url || null)}`,
      `disclosureNote: ${JSON.stringify(disclosure_note || null)}`,
      `authorMetadata: ${JSON.stringify(author_metadata || {})}`,
      '---',
      '',
      content || ''
    ].join('\n');

    // Write file
    const targetFile = path.join(targetLangDir, `${slug}.md`);
    fs.writeFileSync(targetFile, frontmatter, 'utf8');
  }
}

console.log(`Post generation completed successfully!`);

// Helper functions
function getFirstParagraph(content) {
  if (!content) return '';
  const paragraphs = content.split(/\n\s*\n/);
  for (const p of paragraphs) {
    const trimmed = p.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      return trimmed;
    }
  }
  return content.trim();
}

function stripMarkdown(text) {
  return text
    .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1') // link [text](url) -> text
    .replace(/[*_`#]/g, '') // formatting
    .replace(/\s+/g, ' ')
    .trim();
}

function truncateDescription(text) {
  if (text.length <= 160) return text;
  let truncated = text.slice(0, 160);
  const lastSpace = truncated.lastIndexOf(' ');
  if (lastSpace > 120) {
    truncated = truncated.slice(0, lastSpace);
  }
  return truncated.trim() + '...';
}
