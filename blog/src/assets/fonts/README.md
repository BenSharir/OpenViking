Fonts in this directory are imported by `src/themes.css`, so Vite emits hashed files under `dist/assets` for CDN caching.

- `TsangerJinKai02-W04.ttf` and `TsangerJinKai02-W05.ttf` are the Chinese faces used by the kami style.
- `JetBrainsMono.woff2` is used for code, metadata, and compact UI labels.

The Tsanger font files match `tw93/Kami` byte-for-byte. The CSS keeps the local Vite-managed files first and uses the Kami jsDelivr URLs as a fallback.
