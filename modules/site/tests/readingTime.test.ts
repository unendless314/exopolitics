import { describe, it, expect } from 'vitest';
import { calculateReadingTime } from '../src/utils/readingTime';

describe('calculateReadingTime', () => {
  it('should handle empty content', () => {
    expect(calculateReadingTime('')).toBe(1);
  });

  it('should calculate reading time for Latin (English) content', () => {
    // 200 words should be exactly 1 minute
    const englishText = Array(200).fill('word').join(' ');
    expect(calculateReadingTime(englishText)).toBe(1);

    // 300 words should be ceil(1.5) = 2 minutes
    const longerEnglishText = Array(300).fill('word').join(' ');
    expect(calculateReadingTime(longerEnglishText)).toBe(2);
  });

  it('should calculate reading time for CJK content', () => {
    // 300 Chinese characters should be exactly 1 minute
    const chineseText = Array(300).fill('中').join('');
    expect(calculateReadingTime(chineseText)).toBe(1);

    // 450 Chinese characters should be ceil(1.5) = 2 minutes
    const longerChineseText = Array(450).fill('中').join('');
    expect(calculateReadingTime(longerChineseText)).toBe(2);
  });

  it('should calculate reading time for mixed content', () => {
    // 150 Chinese characters (0.5 mins) + 100 English words (0.5 mins) = 1 minute
    const mixedText = Array(150).fill('中').join('') + ' ' + Array(100).fill('word').join(' ');
    expect(calculateReadingTime(mixedText)).toBe(1);

    // 300 Chinese characters (1 min) + 200 English words (1 min) = 2 minutes
    const longerMixedText = Array(300).fill('中').join('') + ' ' + Array(200).fill('word').join(' ');
    expect(calculateReadingTime(longerMixedText)).toBe(2);
  });

  it('should strip markdown and html tags before counting', () => {
    // Boundary fixture: 192 words once markup is removed. If html tags were
    // counted as words, the same text would exceed 200 words and round up to
    // 2 minutes, so this only passes when tags are actually stripped.
    const plainWords = Array(190).fill('word');
    const tags = Array(12).fill('<em>').concat(Array(12).fill('</em>'));
    const textWithMarkup = [...plainWords, '**bold**', '_italic_', ...tags].join(' ');
    // Sanity check the boundary: naive whitespace count would be 216 words.
    expect(textWithMarkup.split(/\s+/).length).toBe(216);
    expect(calculateReadingTime(textWithMarkup)).toBe(1);
  });
});
