# blog-to-pdf — a Claude Code skill

Turn any blog post or article URL into a **print-ready A4 PDF**: light mode, careful pagination (nothing ever cut mid-paragraph or mid-image), typography that matches the site, images laid out beside the text where that saves pages, and a one-page **reader's companion** (TL;DR, key ideas, checkbox action points) at the end — clearly labeled as added by Claude.

It is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills): a `SKILL.md` playbook plus a self-paginating HTML template and a small Python helper module. Claude does the extraction and summarising; the template and helpers do the layout.

## Install

```bash
git clone https://github.com/b11nmayavi-coder/blog-to-pdf-skill ~/.claude/skills/blog-to-pdf
```

Requirements:

- Python 3 with Pillow (`pip install pillow`)
- `curl`
- Google Chrome or Chromium (headless PDF printing)
- poppler-utils for QA renders (`sudo apt install poppler-utils` / `brew install poppler`)
- optional: [rclone](https://rclone.org) with a `gdrive` remote, if you want PDFs uploaded to Google Drive automatically (`rclone config create gdrive drive`)

## Use

In Claude Code:

```
/blog-to-pdf https://example.com/some-article
```

or just paste a URL and ask for a printable PDF. Claude fetches the page, rebuilds the article as a flat sequence of blocks, embeds fonts and images as data URIs, writes the companion page from the full text, renders the PDF with headless Chrome, visually reviews every page, and delivers:

- the PDF, named `<site>-<slug>.pdf`, uploaded to `Blog PDFs/<date>/` on Google Drive if you have the `gdrive` rclone remote, otherwise saved to `~/Desktop/`
- an interactive preview artifact (when the Artifact tool is available) that matches the PDF page for page

## How it works

- **`print_template.html`** — the document shell. A JavaScript paginator measures each top-level block and flows them into 794×1122 px sheets (A4 at 96 dpi). Blocks are atomic; headings stay with their next block; rules at page boundaries are dropped; over-long lists split between items with numbering preserved; `class="break-page"` forces a new page. `@page` CSS makes Chrome print each sheet as one A4 page with no browser header/footer.
- **`helpers.py`** — stdlib + Pillow only: fetch and container/block extraction without an HTML parser, Google Fonts embedding (latin subset, data URIs), image optimisation and orientation tagging, side-by-side layout for portrait images with captions, a tofu-glyph check, the companion page, headless-Chrome rendering and a contact-sheet generator for QA.
- **`examples/build_example.py`** — a full build script for a Substack post, the pattern Claude adapts per site.

## Customising

- Change where PDFs go in the **Delivery** section of `SKILL.md`.
- Per-site CSS goes through the `/*EXTRA*/` placeholder (fonts, heading sizes, title alignment).
- Base typography lives in the `.doc` rules of `print_template.html`.

## License

MIT — see [LICENSE](LICENSE).
