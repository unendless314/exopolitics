export const localeProfiles = {
  zh: { label: "繁體中文", langTag: "zh-Hant", dir: "ltr" },
  en: { label: "English", langTag: "en-US", dir: "ltr" },
  ja: { label: "日本語", langTag: "ja-JP", dir: "ltr" },
} as const;

export type SupportedLanguages = keyof typeof localeProfiles;

export const uiTranslations = {
  zh: {
    'nav.home': '首頁',
    'nav.timeline': '時間線',
    'nav.archives': '歸檔列表',
    'nav.stats': '數據統計',
    'post.originalSource': '原始來源',
    'post.disclosure': 'AI 聲明',
    'post.readingTime': '閱讀時間',
    'post.minutes': '分鐘',
    'post.author': '作者',
    'post.editor': '編輯',
    'post.writerType': '寫作者類型',
    'post.sourceModule': '來源模組',
    'post.canonicalUrl': '原文連結',
    'stats.title': '全局統計數據',
    'stats.totalActive': '已發布項目總數',
    'stats.totalWithdrawn': '已撤回項目總數',
    'stats.lastRun': '最後更新時間',
    'stats.oldestArchive': '最舊歸檔月份',
    'stats.archiveMonthCount': '歸檔月份總數',
    'archive.title': '按月歸檔列表',
    'archive.monthTitle': '{month} 歸檔項目',
    'archive.empty': '無項目',
    'archive.totalItems': '共 {count} 個項目',
    'theme.toggle': '切換主題',
    'meta.homeDesc': '最新 UAP/UFO 新聞快報，來自全球科學與官方來源的彙整資訊。',
    'meta.archivesDesc': '按月瀏覽所有歷史 UAP/UFO 新聞彙整存檔。',
    'meta.archiveMonthDesc': '{month} 月份的 UAP/UFO 新聞存檔項目。',
    'meta.statsDesc': 'UAP/UFO 彙整內容的全局資料庫同步與發布統計數據。',
    'post.updatedUpstream': '上游更新時間',
    'stats.metric': '指標',
    'stats.latestIndexCapacity': '最新索引容量'
  },
  en: {
    'nav.home': 'Home',
    'nav.timeline': 'Timeline',
    'nav.archives': 'Archives',
    'nav.stats': 'Stats',
    'post.originalSource': 'Original Source',
    'post.disclosure': 'Disclosure',
    'post.readingTime': 'Reading Time',
    'post.minutes': 'min',
    'post.author': 'Author',
    'post.editor': 'Editor',
    'post.writerType': 'Writer Type',
    'post.sourceModule': 'Source Module',
    'post.canonicalUrl': 'Canonical Link',
    'stats.title': 'Global Statistics',
    'stats.totalActive': 'Total Active Items',
    'stats.totalWithdrawn': 'Total Withdrawn Items',
    'stats.lastRun': 'Last Updated',
    'stats.oldestArchive': 'Oldest Archive Month',
    'stats.archiveMonthCount': 'Total Archive Months',
    'archive.title': 'Monthly Archives',
    'archive.monthTitle': 'Archives for {month}',
    'archive.empty': 'No items found',
    'archive.totalItems': '{count} items total',
    'theme.toggle': 'Toggle theme',
    'meta.homeDesc': 'Latest news alerts and disclosure updates about UAP/UFO aggregated from global scientific and official sources.',
    'meta.archivesDesc': 'Browse all historical aggregated UAP and UFO news alerts by month.',
    'meta.archiveMonthDesc': 'Historical aggregated UAP and UFO news alerts for the month of {month}.',
    'meta.statsDesc': 'Global database synchronization and publishing statistics for UAP/UFO aggregated content.',
    'post.updatedUpstream': 'Updated Upstream',
    'stats.metric': 'Metric',
    'stats.latestIndexCapacity': 'Latest Index Capacity'
  },
  ja: {
    'nav.home': 'ホーム',
    'nav.timeline': 'タイムライン',
    'nav.archives': 'アーカイブ',
    'nav.stats': '統計データ',
    'post.originalSource': '一次情報源',
    'post.disclosure': 'AI 開示事項',
    'post.readingTime': '読了時間',
    'post.minutes': '分',
    'post.author': '著者',
    'post.editor': '編集者',
    'post.writerType': 'ライタータイプ',
    'post.sourceModule': 'ソースモジュール',
    'post.canonicalUrl': '原文リンク',
    'stats.title': 'グローバル統計',
    'stats.totalActive': '公開済みアイテム総数',
    'stats.totalWithdrawn': '非公開アイテム総数',
    'stats.lastRun': '最終更新日時',
    'stats.oldestArchive': '最古のアーカイブ月',
    'stats.archiveMonthCount': 'アーカイブ月数',
    'archive.title': '月別アーカイブ',
    'archive.monthTitle': '{month} のアーカイブ',
    'archive.empty': '記事が見つかりません',
    'archive.totalItems': '全 {count} 件',
    'theme.toggle': 'テーマ切り替え',
    'meta.homeDesc': 'UAP/UFOに関する最新ニュースアラートと開示情報。世界の科学・公式ソースから集約。',
    'meta.archivesDesc': 'UAP/UFOニュースアラートの月別アーカイブを閲覧。',
    'meta.archiveMonthDesc': '{month}のUAP/UFOニュースアーカイブ項目。',
    'meta.statsDesc': 'UAP/UFO集約コンテンツのグローバルデータベース同期と公開統計。',
    'post.updatedUpstream': 'アップストリーム更新',
    'stats.metric': '指標',
    'stats.latestIndexCapacity': '最新インデックス容量'
  }
} as const;

export type UIKeys = keyof typeof uiTranslations['en'];

/**
 * Translation helper for localized UI elements.
 */
export function useTranslations(lang: string) {
  const currentLang = (lang in uiTranslations ? lang : 'en') as SupportedLanguages;
  return function t(key: UIKeys, params?: Record<string, string | number>): string {
    let translation: string = uiTranslations[currentLang][key] || uiTranslations['en'][key] || '';
    if (params) {
      Object.entries(params).forEach(([paramKey, paramVal]) => {
        translation = translation.replace(`{${paramKey}}`, String(paramVal));
      });
    }
    return translation;
  };
}

/**
 * Helper to get corresponding paths for all supported locales.
 */
export function getLocalePaths(url: URL): Array<{ lang: SupportedLanguages; path: string }> {
  const pathname = url.pathname;
  const match = pathname.match(/^\/([a-z]{2})(?:\/|$)(.*)/);
  let subPath = pathname;
  if (match) {
    subPath = '/' + match[2];
  }
  return (Object.keys(localeProfiles) as SupportedLanguages[]).map((lang) => {
    const cleanSubPath = subPath.startsWith('/') ? subPath.slice(1) : subPath;
    const path = `/${lang}/${cleanSubPath}`;
    return { lang, path };
  });
}

/**
 * Formats absolute ISO-8601 date to standard display precision: YYYY-MM-DD HH:mm (UTC).
 */
export function formatAbsoluteDateTime(dateString: string): string {
  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;
    const yyyy = date.getUTCFullYear();
    const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(date.getUTCDate()).padStart(2, '0');
    const hh = String(date.getUTCHours()).padStart(2, '0');
    const min = String(date.getUTCMinutes()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd} ${hh}:${min} UTC`;
  } catch {
    return dateString;
  }
}
