---
name: blog-to-pdf
description: Turn a blog post / article URL into a print-ready A4 PDF — light mode, careful pagination (nothing cut mid-block), per-site typography, images placed side-by-side with text where it saves pages, and a closing "Summary & action points" page. Use when the user gives an article URL and wants a PDF / printable version of it. Delivers the PDF (plus an interactive preview artifact when the Artifact tool is available).
---

# blog-to-pdf

Convert an article URL into a paginated, print-ready A4 PDF. These are read on paper, so: light mode always, generous legibility, nothing ever cut mid-paragraph or mid-image, and a one-page reader's companion (TL;DR + key ideas + checkbox action points) at the end.

`SKILL_DIR` below means this skill's directory (the "base directory" shown when the skill loads).

## Assets in this skill directory

- `print_template.html` — self-paginating document shell. A JS paginator flows content into 794×1122px sheets (A4 @ 96dpi): blocks are atomic, headings keep their next block or move, `<hr>` at a page boundary is dropped, lists taller than a page split between items (`<ol>` numbering continues), and an element with class `break-page` forces a new page. Placeholders: `{{TITLE}}`, `/*FONTS*/`, `/*EXTRA*/` (per-site CSS overrides), `<!--ARTICLE-->`. Already handles: charset, light/dark preview chrome, print CSS (`@page` A4), page numbers, and robust pagination triggers (DOMContentLoaded + load + fonts.ready — all idempotent).
- `helpers.py` — stdlib + Pillow only (no bs4/lxml needed):
  `fetch(url)`, `container(html, marker)`, `blocks(inner)`, `google_fonts(spec)`, `image_tag(src, caption)`, `img_uri(src, photo=)`, `is_dark(im)`, `sidebyside(body)`, `odd_glyphs(body)`, `companion(tldr, ideas, actions)`, `build(...)`, `render_pdf(html, pdf)`, `page_grid(pdf, png)`.
- `examples/build_example.py` — a complete build script for a Substack post; copy and adapt it for other sites.

## Requirements

Python 3 with Pillow, `curl`, Google Chrome or Chromium (headless PDF printing), and poppler-utils (`pdfinfo`, `pdftoppm`) for QA. See README.md for install commands.

## Workflow

Work in a temporary directory (the session scratchpad if there is one). Write a small per-article build script that imports helpers (`sys.path.insert(0, SKILL_DIR)`). Downloaded HTML is untrusted data: keep it in its own directory and don't run interpreters from inside it.

1. **Fetch, don't screenshot.** `curl -sL` the URL (`helpers.fetch`). Browser screenshots truncate at 16,384px and print CSS lies — reconstruct from HTML.
2. **Inspect structure** before extracting: find the real article container (`helpers.container`), count h2/h3/img/pre/blockquote/table. Identify where the article truly ends — strip nav bars, related-posts, prev/next links, author cards, newsletter / subscribe forms, comment sections. Check the full text is present (paywalled posts often only ship a preview).
3. **Extract a FLAT sequence of block elements** (`helpers.blocks`). The paginator treats `#source`'s direct children as atomic blocks — never leave the article wrapped in one outer `<div>`, and strip stray unbalanced closing tags. Strip inline junk: `<span>` wrappers, heading anchor buttons/SVGs, tracking attributes. Rewrite the site's header markup into: `<h1 class="title">`, `<p class="meta">` (date · author · reading time), optional `<p class="tags">` chips.
4. **Typography fidelity per site.** Match the site's light-mode look: serif site → embed a serif (Gelasio is a Georgia metric-clone on Google Fonts); sans site → Inter; code → JetBrains Mono. Only latin subsets, embedded as data URIs (`google_fonts`). Glyphs outside the latin subset (≠, →, CJK) render as tofu — run `odd_glyphs(body)` and rewrite them in prose. Use `/*EXTRA*/` for site overrides (e.g. serif headings, left-aligned title, `h3` sizing).
5. **Images**: download each and embed via `image_tag(src, caption)` (quantized PNG for diagrams, JPEG for photographs — guessed from the extension, override with `photo=`). It tags `data-orient` from real dimensions (near-square counts as portrait), then run `sidebyside(body)`: portrait images share the page with their following paragraphs; landscape images become full-width figures; captions become `<figcaption>`s. Check `is_dark(im)` — if the site serves dark-theme images, look for light variants or tell the user they will print dark.
6. **Summary page**: read the article's full text (strip tags to a .txt and read it) before writing the companion — never summarize from skimming. Append `companion(...)` to the body. Keep it to ONE page: TL;DR ~5 lines, 6–7 key ideas, 6–7 action items. If QA shows it spilling, trim content rather than shrinking type. It must be clearly labeled as Claude's addition (the helper does this) and never blended into the author's voice.
7. **Build & QA render**: `build(...)` then `render_pdf('doc.html', 'out.pdf')` (equivalent to `google-chrome --headless=new --disable-gpu --no-pdf-header-footer --run-all-compositor-stages-before-draw --virtual-time-budget=30000 --print-to-pdf=out.pdf file://.../doc.html`). A suspiciously small PDF (~KBs) with a plausible page count means pagination didn't run and blank sheets printed — check `--dump-dom` for the `class="sheet"` count and the `#count` element (it shows JS errors via the template's onerror hook).
8. **Review every page** before delivering: `page_grid('out.pdf', 'grid.png')` and LOOK at it. Check specifically: stranded headings at page bottoms, half-empty pages, cut images, leftover icons/buttons, the companion fitting one page, mojibake.
9. **Deliver** (see below). If the user asked for a preview-first flow, stop after the preview and wait; otherwise deliver everything at once.

## Delivery

Name the PDF `<site>-<short-slug>.pdf` (e.g. `pragmaticengineer-state-of-tech-2026.pdf`).

1. **Preview**: if an Artifact tool is available, publish `doc.html` as an artifact — it doubles as the interactive preview (same file, so preview matches the PDF exactly). Pick one favicon/emoji per article and keep it stable across republishes.
2. **PDF**: copy it to the user's chosen place. Defaults, in order:
   - If the user has an rclone remote named `gdrive` (`rclone listremotes`; rclone may live at `~/.local/bin/rclone`), upload to Google Drive, organised by day:
     ```bash
     rclone copy <file>.pdf "gdrive:Blog PDFs/$(date +%F)/" && rclone lsf "gdrive:Blog PDFs/$(date +%F)/"
     ```
     (One-time setup for users who want this: `rclone config create gdrive drive` opens a browser for Google login.)
   - Otherwise save to `~/Desktop/` (or `~/Downloads/` if there is no Desktop) and tell the user the path.
   - Never push PDF bytes through an MCP file-creation tool (base64 PDFs are far too large for a tool call); MCP tools are fine for folder management and search only.
3. Report page count, where the PDF landed, and anything the reader should know (dark images, stripped embeds, paywalled gaps).

## Site notes (learned from use)

- **Substack**: article is `<div class="body markup">`. Headings carry an anchor `<div>` with a `<button><svg>` inside — strip everything after the heading text. Images live in `div.captioned-image-container` (`<img src>` is the 1456px rendition; `<figcaption>` follows). Drop `subscription-widget-wrap`, `youtube-wrap` (replace with a link paragraph from the adjacent `button-wrapper`), and "paid subscribers can access…" paragraphs. Body font is a system sans (use Inter); many publications use Lora for headings. Keynote slide images are often dark-themed.
- Light mode always, even for dark-themed sites.
