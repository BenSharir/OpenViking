# CLAUDE.md — Authoring guide for OpenViking Blog posts

You are an agent adding a post to the **OpenViking Blog**. This document is the contract.

---

## What you are building

A single blog post lives in **`src/posts/<slug>/`**. The shell discovers it via a JS-side registry. To add a post you write one file:

```
src/posts/my-essay/index.jsx
```

The shell renders it inside a themed Article container, supplies it with the active language and theme, generates the table of contents from your headings, fills the byline from your meta, and wires routing automatically.

Posts **must not**:

- Import their own CSS, fonts, or theme styles. Theming is global; posts only compose primitives.
- Hard-code colors, fonts, or pixel values. Use the primitives below; they pick up `--th-*` tokens.
- Branch visuals on `theme`. The point of theming is that posts don't care.
- Fetch from the network at render time. Embed everything you need (SVGs, JSON) in `src/posts/<slug>/`.

---

## File layout

```
src/posts/<slug>/
  index.jsx          # required — default export registers the post
  assets/            # optional — local images/SVGs for this post
    figure-1.svg
```

Use the **slug** as the URL segment (`#/post/<slug>`). It must be lowercase-kebab-case, stable for the life of the post (treat it like a URL primary key — never rename), and unique across the site.

For shared assets (covers reused across posts, author avatars), add them under `public/assets/`:

```
public/assets/
  covers/<name>.svg
  avatars/<name>.svg
```

---

## The registration contract

Every post file is an ES module that default-exports a registration object:

```jsx
/* src/posts/my-essay/index.jsx */
import React from 'react';
import { Article, Lead, P, H2 } from '../../blog-components';

const MyEssay = ({ t }) => (
  <Article>
    <Lead>{t({ en: 'A short essay.', zh: '一篇短文。' })}</Lead>
    <P>{t({ en: 'Body text.', zh: '正文。' })}</P>
  </Article>
);

export default {
  id: 'my-essay',
  Component: MyEssay,
  meta: { /* see schema below */ },
};
```

After writing the file, **add an import and register call in `src/posts/index.js`**:

```js
import myEssay from './my-essay/index.jsx';
// add to the array:
[..., myEssay].forEach(registerPost);
```

---

## Writing process

Write bilingual content in this order:

1. **Write the English version first.** Get the structure, examples, and technical content right.
2. **Translate to Chinese.** Produce a faithful translation.
3. **Optimize the Chinese version for natural tone.** Follow the [shuorenhua](https://github.com/MrGeDiao/shuorenhua) guidelines: no AI filler, no template openers/closers ("In this article we will explore..."), no empty adjectives. Write like you're explaining to a colleague, not writing a press release.

---

## Meta schema

Every field is required unless marked optional. Strings that face the reader **must** be locale objects (`{ en, zh }`) unless this post truly only ships in one language.

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | `{ en, zh, ... }` | yes | Plain text. The shell renders it. |
| `description` | `{ en, zh, ... }` | yes | One sentence. Shown on the index card and below the post title. |
| `cover` | `string` | yes | Path to an SVG/PNG. Aspect ratio 16:9 or 16:7 is best. |
| `publishedAt` | `'YYYY-MM-DD'` | yes | ISO date. Drives ordering. |
| `updatedAt` | `'YYYY-MM-DD'` | optional | Render only when meaningfully different from `publishedAt`. |
| `readingTime` | `number` (minutes) | yes | Round to nearest minute. ~250 words/min for prose, less for reference. |
| `category` | `{ en, zh, ... }` | optional | Single short label, e.g. `{ en: 'Engineering', zh: '工程' }`. |
| `tags` | `string[]` | yes | Lowercase, ASCII, kebab-case. Reuse existing tags before inventing one. |
| `languages` | `string[]` | yes | Locale codes the post supports, e.g. `['en', 'zh']`. The shell shows a fallback notice when the reader's lang is missing. |
| `authors` | `Author[]` | yes | At least one. See below. |

`Author`:

| Field | Type | Required |
|---|---|---|
| `name` | `string` | yes |
| `github` | `string` (handle, no URL) | optional, recommended |
| `avatar` | `string` (path) | optional |
| `role` | `{ en, zh, ... }` | optional |

---

## Component contract

The shell calls your component with these props (also available via the `useBlog` hook):

| Prop | Type | What |
|---|---|---|
| `lang` | string | Active locale, narrowed to one your post supports (driven by `meta.languages`). |
| `theme` | string | Active theme id. Do not branch visuals on it. |
| `t` | `(value) => string` | Locale picker. `t({ en: 'A', zh: '甲' })` → `'A'` or `'甲'`. Pass through plain strings unchanged. |
| `formatDate` | `(iso) => string` | Locale-formatted date. |
| `navigate` | `(href) => void` | Programmatic router. `navigate('#/post/other-slug')`. |

Hooks (use anywhere inside your component tree):

```jsx
const { lang, theme, t, formatDate, navigate } = useBlog();
```

**Authoring rule:** wrap every reader-facing string in `t({...})`, even if you only ship one language today.

---

## Primitives — your toolbox

Import from `../../blog-components`. Themed via `themes.css`.

**Structure** — `Article` (root, required), `Section`, `Spacer h="sm|md|lg|xl"`, `Hr`, `Hr ornament`.

**Headings** — `H1` (rare; the shell renders the post title already), `H2`, `H3`, `H4`. Auto-id from text — that is what feeds the TOC.

**Text** — `P`, `P dropCap`, `Lead`, `Small`, `Strong`, `Em`, `InlineCode`, `Kbd`, `Mark`.

**Links** — `A href` (auto-detects internal `#/...` vs external `https://...`).

**Lists** — `Ul`, `Ul marker="check"`, `Ol`, `Li`, `Dl`/`Dt`/`Dd`.

**Code** — `Pre lang="js" filename="...">{...}</Pre>`.

**Quotes** — `Quote cite="..."` for block quotes; `Pull` for pull quotes (use sparingly — one or two per post).

**Figures** — `Figure src caption credit size="sm|md|lg" frame="plain|frame|bleed"`.

**Callouts** — `Callout type="note|tip|warn|info|quote"`.

**Aside** — `Aside`. A subtle margin-note style.

**Tables** — `Table headers={[...]} rows={[[...], [...]]} caption={...}`.

**Layout** — `Cols count={2|3}` with `Col` children. Collapses to one column on mobile.

**Tags** — `Tag` for inline tags.

---

## Custom components

Posts can define custom React components inside their own `index.jsx` for interactive or visual elements beyond the primitives (diagrams, badges, step indicators, etc.). Keep them scoped to the post file. Use `var(--th-*)` tokens for colors so they work across all themes.

If the same custom component appears in three or more posts, lift it into `blog-components.jsx`.

---

## House style — content rules

- **One thesis per post.** If you have two, write two posts.
- **Lead first.** A `Lead` paragraph before any heading. Treat it as the dek.
- **Sections, not headers.** `H2` introduces a section. If you only have one section, you don't need any H2s.
- **No filler.** Don't pad with statistics, icons, or "in this post you will learn..." preambles. Don't summarize what the post is about; just write it.
- **Pull quotes are a budget.** Maximum 2 per post.
- **Code blocks are real code.** No `// ...` ellipsis hand-waving in the middle of an example.
- **Translation parity.** Both languages should say the same thing, not just be word-for-word equivalents. Idioms travel.
- **Cover images** must work at any aspect ratio from 16:7 (post header) to 16:10 (index card) to 4:3 (Garden theme card). Test in at least two themes.

---

## Checklist before opening a PR

- [ ] Slug is lowercase-kebab, unique, and matches the folder name.
- [ ] `meta.title`, `description`, `category` are locale objects covering every code in `meta.languages`.
- [ ] `cover` exists and looks right at 16:7 and 4:3.
- [ ] `publishedAt` is set; `readingTime` is honest.
- [ ] At least one author, with `github` if known.
- [ ] Tags are reused from the existing set when possible.
- [ ] Every reader-facing string passes through `t()`.
- [ ] No imports of CSS, fonts, theme files.
- [ ] No hard-coded colors or pixel values.
- [ ] Post is registered in `src/posts/index.js`.
- [ ] You ran the site in at least two themes and confirmed nothing breaks.

---

## Reference

See existing posts in `src/posts/` for examples of the registration pattern and custom component usage.
