import { describe, it, expect } from 'vitest';
import { useTranslations, getLocalePaths, formatAbsoluteDateTime } from '../src/utils/i18n';

describe('i18n helpers', () => {
  describe('useTranslations', () => {
    it('should translate correctly for supported languages', () => {
      const tZh = useTranslations('zh');
      const tEn = useTranslations('en');
      const tJa = useTranslations('ja');

      expect(tZh('nav.home')).toBe('首頁');
      expect(tEn('nav.home')).toBe('Home');
      expect(tJa('nav.home')).toBe('ホーム');
    });

    it('should fallback to English for unsupported languages', () => {
      const tEs = useTranslations('es');
      expect(tEs('nav.home')).toBe('Home');
    });

    it('should replace parameters correctly', () => {
      const tEn = useTranslations('en');
      expect(tEn('archive.monthTitle', { month: '2026-06' })).toBe('Archives for 2026-06');

      const tZh = useTranslations('zh');
      expect(tZh('archive.totalItems', { count: 42 })).toBe('共 42 個項目');
    });
  });

  describe('getLocalePaths', () => {
    it('should generate path alternate URLs for other languages from simple path', () => {
      const url = new URL('https://example.com/zh/posts/my-cool-post');
      const paths = getLocalePaths(url);

      expect(paths).toEqual([
        { lang: 'zh', path: '/zh/posts/my-cool-post' },
        { lang: 'en', path: '/en/posts/my-cool-post' },
        { lang: 'ja', path: '/ja/posts/my-cool-post' }
      ]);
    });

    it('should handle root path correctly', () => {
      const url = new URL('https://example.com/zh');
      const paths = getLocalePaths(url);

      expect(paths).toEqual([
        { lang: 'zh', path: '/zh/' },
        { lang: 'en', path: '/en/' },
        { lang: 'ja', path: '/ja/' }
      ]);
    });

    it('should handle root with trailing slash correctly', () => {
      const url = new URL('https://example.com/zh/');
      const paths = getLocalePaths(url);

      expect(paths).toEqual([
        { lang: 'zh', path: '/zh/' },
        { lang: 'en', path: '/en/' },
        { lang: 'ja', path: '/ja/' }
      ]);
    });
  });

  describe('formatAbsoluteDateTime', () => {
    it('should format ISO timestamp to standard UTC date and time precision', () => {
      const stamp = '2026-06-21T19:15:13Z';
      expect(formatAbsoluteDateTime(stamp)).toBe('2026-06-21 19:15 UTC');
    });

    it('should return original string if input is invalid', () => {
      expect(formatAbsoluteDateTime('not-a-date')).toBe('not-a-date');
    });
  });
});
