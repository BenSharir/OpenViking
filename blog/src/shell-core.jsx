import { useState, useEffect, useMemo, useCallback } from 'react';

/* ---------- hash router ---------- */

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/';
  const [pathPart, queryPart = ''] = raw.split('?');
  const segs = pathPart.split('/').filter(Boolean);
  const query = {};
  for (const kv of queryPart.split('&').filter(Boolean)) {
    const [k, v = ''] = kv.split('=');
    query[decodeURIComponent(k)] = decodeURIComponent(v);
  }
  let route = { name: 'index' };
  if (segs[0] === 'post' && segs[1]) route = { name: 'post', slug: segs[1] };
  return { route, query, raw };
}

export function buildHash(route, query) {
  let path = '/';
  if (route.name === 'post') path = `/post/${route.slug}`;
  const qs = Object.entries(query)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  return `#${path}${qs ? '?' + qs : ''}`;
}

export function useHashRouter() {
  const [state, setState] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const onHash = () => setState(parseHash(location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const navigate = useCallback((href) => {
    if (typeof href === 'string') {
      const h = href.startsWith('#') ? href : '#' + href;
      if (location.hash !== h) location.hash = h;
    } else {
      const h = buildHash(href.route || state.route, { ...state.query, ...(href.query || {}) });
      if (location.hash !== h) location.hash = h;
    }
  }, [state]);
  const setQuery = useCallback((patch) => {
    const next = { ...state.query, ...patch };
    Object.keys(next).forEach(k => { if (next[k] == null || next[k] === '') delete next[k]; });
    const h = buildHash(state.route, next);
    if (location.hash !== h) location.hash = h;
  }, [state]);
  return { ...state, navigate, setQuery };
}

/* ---------- i18n / locale helpers ---------- */

export const LANGS = [
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'zh', label: '中文', short: '中' },
];

export const SHELL_STRINGS = {
  en: {
    siteName: 'OpenViking Blog',
    siteSub: 'Engineering notes',
    indexEyebrow: '2026',
    indexTitle: 'Blog in Public.',
    indexLede: 'Technical notes from the OpenViking team — on agents, protocols, and the systems behind them.',
    countLabel: (n) => `${n} essays`,
    filterAll: 'All',
    sortNewest: 'Newest first',
    sortOldest: 'Oldest first',
    backToIndex: '← All essays',
    publishedOn: 'Published',
    updatedOn: 'Updated',
    readingTime: (m) => `${m} min read`,
    by: 'by',
    contents: 'Contents',
    prev: 'Previous',
    next: 'Next',
    relatedTitle: 'Continue reading',
    notFoundTitle: 'Nothing here',
    notFoundBody: 'That essay does not exist. It may have been a dream.',
    langLabel: 'Language',
    themeLabel: 'Theme',
    notAvailableLang: 'This essay is not yet translated. Showing English.',
    tags: 'Tags',
  },
  zh: {
    siteName: 'OpenViking 博客',
    siteSub: '技术笔记',
    indexEyebrow: '2026',
    indexTitle: '感受 AI。',
    indexLede: 'OpenViking 团队的技术笔记 — 关于 Agent、协议，以及背后的系统。',
    countLabel: (n) => `${n} 篇文章`,
    filterAll: '全部',
    sortNewest: '最新优先',
    sortOldest: '最早优先',
    backToIndex: '← 所有文章',
    publishedOn: '发布于',
    updatedOn: '更新于',
    readingTime: (m) => `阅读约 ${m} 分钟`,
    by: '作者',
    contents: '目录',
    prev: '上一篇',
    next: '下一篇',
    relatedTitle: '继续阅读',
    notFoundTitle: '此处空空如也',
    notFoundBody: '这篇文章不存在,也许只是一场梦。',
    langLabel: '语言',
    themeLabel: '主题',
    notAvailableLang: '本文尚未翻译,显示英文版本。',
    tags: '标签',
  },
};

export function useShellStrings(lang) {
  return SHELL_STRINGS[lang] || SHELL_STRINGS.en;
}

export function makeFormatDate(lang) {
  return (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const locale = lang === 'zh' ? 'zh-CN' : 'en-US';
      return d.toLocaleDateString(locale, { year: 'numeric', month: lang === 'zh' ? 'long' : 'short', day: 'numeric' });
    } catch { return iso; }
  };
}

/* ---------- theme persistence ---------- */

export const THEMES = [
  { id: 'folio',   label: { en: 'Folio',   zh: '文集' }, blurb: { en: 'Serif · warm',    zh: '衬线 · 暖色' } },
  { id: 'console', label: { en: 'Console', zh: '终端' }, blurb: { en: 'Mono · dark',     zh: '等宽 · 暗色' } },
  { id: 'garden',  label: { en: 'Garden',  zh: '园圃' }, blurb: { en: 'Serif · gentle',  zh: '衬线 · 柔和' } },
  { id: 'kami',    label: { en: 'Kami',    zh: '纸' },   blurb: { en: 'Ink · parchment', zh: '墨蓝 · 纸色' } },
];

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme === 'console' ? 'dark' : 'light';
}
