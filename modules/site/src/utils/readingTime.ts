/**
 * Calculates estimated reading time for CJK and Latin mixed content.
 * Latin WPM (Words Per Minute): 200
 * CJK CPM (Characters Per Minute): 300
 */
export function calculateReadingTime(content: string): number {
  if (!content) return 1;

  // Strip HTML tags and markdown punctuation
  const cleanText = content
    .replace(/<[^>]*>/g, "")
    .replace(/[#*`_\[\]()\-!?,.;:"']/g, " ");

  // Match CJK characters (Han, Hiragana, Katakana)
  const cjkRegex = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]/g;
  const cjkMatches = cleanText.match(cjkRegex);
  const cjkCount = cjkMatches ? cjkMatches.length : 0;

  // Strip CJK characters to count remaining Latin words
  const nonCjkText = cleanText.replace(cjkRegex, " ");
  const words = nonCjkText.trim().split(/\s+/).filter(Boolean);
  const wordCount = words.length;

  // Sum the reading time for both scripts
  const readingTime = (cjkCount / 300) + (wordCount / 200);

  return Math.max(1, Math.ceil(readingTime));
}
