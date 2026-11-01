#!/usr/bin/env python3
"""Article URL -> print-ready A4 PDF, unattended.

The hosted counterpart of the blog-to-pdf skill: it reuses the skill's helpers
and template, but replaces Claude's per-site judgement with a generic
extractor, a CSS font heuristic, a Claude API summary (Ollama Cloud backup)
and automated checks.

    python pipeline.py <url> [--no-upload]
"""
import html, json, os, re, shutil, subprocess, sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import lxml.html
from lxml import etree
import trafilatura

SKILL_DIR = Path(__file__).resolve().parent.parent   # this file lives in <skill>/hosted/
sys.path.insert(0, str(SKILL_DIR))
from helpers import (UA, google_fonts, image_tag, is_dark, sidebyside, companion,  # noqa: E402
                     build, render_pdf, site_font, dom_check)
import summary  # noqa: E402

DATA = Path(os.environ.get('BLOGPDF_DATA', Path.home() / '.local/share/blog-pdf')).expanduser()
OUT = DATA / 'out'
RCLONE = os.environ.get('RCLONE') or shutil.which('rclone') or str(Path.home() / '.local/bin/rclone')
DRIVE_DIR = os.environ.get('DRIVE_DIR', 'gdrive:Blog PDFs')   # empty = keep PDFs local only

# Containers that, when present, beat the generic extractor (they keep images,
# captions and code blocks exactly as the author laid them out).
MARKERS = [
    ('substack', '//div[contains(concat(" ", @class, " "), " body ") and contains(@class, "markup")]'),
    ('jekyll', '//section[contains(@class, "page__content")]'),
    ('ghost', '//div[contains(@class, "gh-content")]'),
    ('wordpress', '//div[contains(@class, "entry-content")]'),
    ('generic', '//div[contains(@class, "post-content") or contains(@class, "article-content")]'),
]
DROP = ('script', 'style', 'noscript', 'svg', 'button', 'form', 'iframe', 'video', 'audio',
        'nav', 'aside', 'footer', 'input', 'select', 'textarea', 'template')
DROP_CLASS = re.compile(r'subscri|newsletter|share|social|related|comment|author-?bio|'
                        r'advert|promo|youtube-wrap|button-wrapper|footnote-anchor-wrap|paywall', re.I)
BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'blockquote', 'pre', 'table', 'hr', 'figure', 'img'}
KEEP_ATTR = {'href', 'src', 'alt', 'colspan', 'rowspan', 'start'}
NAV_TAIL = re.compile(r'\b(previous|next|latest|older|newer)\s+(article|post|entry)|^\s*(share|tags?|filed under|related)\b', re.I)
GLYPHS = {'→': '->', '←': '<-', '⇒': '=>', '≠': '!=', '≤': '<=', '≥': '>=', '≈': '~',
          '✓': '(yes)', '✔': '(yes)', '✗': '(no)', '✘': '(no)', '×': 'x', '−': '-', '⁠': '', '​': ''}


def fetch(url):
    """(status, final_url, text) — status lets us refuse 404 pages instead of printing them."""
    r = subprocess.run(['curl', '-sL', '-A', UA, '--max-time', '40', '-w', '\n%{http_code} %{url_effective}', url],
                       capture_output=True)
    body, _, tail = r.stdout.rpartition(b'\n')
    code, _, final = tail.decode().partition(' ')
    return int(code or 0), final or url, body.decode('utf-8', 'replace')


def words(h):
    return len(re.sub(r'<[^>]+>', ' ', h).split())


def best_img_src(img, base):
    src = img.get('src') or img.get('data-src') or ''
    srcset = img.get('srcset') or img.get('data-srcset') or ''
    if srcset:   # widest candidate up to ~1600w
        cands = []
        for c in re.split(r',\s+', srcset):   # CDN URLs (Substack) contain bare commas
            bits = c.strip().split()
            w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith('w') and bits[1][:-1].isdigit() else 0
            if bits:
                cands.append((w if w <= 1600 else -w, bits[0]))
        if cands:
            src = max(cands)[1]
    return urljoin(base, src) if src and not src.startswith('data:') else ''


def flatten(el, base, out):
    """Walk a cleaned element tree and emit atomic block elements as lxml nodes
    or ('img', src, caption) tuples. Wrapper divs/sections are unwrapped."""
    for ch in list(el):
        if not isinstance(ch.tag, str):
            continue
        tag = ch.tag.lower()
        cls = (ch.get('class') or '') + ' ' + (ch.get('id') or '')
        if tag in DROP or DROP_CLASS.search(cls):
            continue
        imgs = ch.xpath('.//img') if tag not in ('img',) else [ch]
        if tag == 'figure' or (tag in ('div', 'picture', 'a', 'p') and imgs and words(lxml.html.tostring(ch, encoding='unicode')) < 40
                               and not ch.xpath('.//p[string-length(normalize-space()) > 120]')):
            if imgs:
                cap = ch.xpath('.//figcaption')
                cap = clean_inline(cap[0], base) if cap else ''
                for im in imgs[:1]:
                    src = best_img_src(im, base)
                    if src:
                        out.append(('img', src, cap or ''))
                continue
        if tag == 'img':
            src = best_img_src(ch, base)
            if src:
                out.append(('img', src, ''))
            continue
        if tag == 'table' and (ch.xpath('.//p|.//table|.//br/following-sibling::br') or len(ch.xpath('.//tr')) == 1):
            flatten_layout_table(ch, base, out)   # tables used for page layout (paulgraham.com)
            continue
        if tag in BLOCK:
            out.append(ch)
        elif tag in ('div', 'section', 'article', 'main', 'header', 'span', 'center', 'picture', 'details'):
            flatten(ch, base, out)
        elif (ch.text_content() or '').strip():   # stray inline/unknown element with text
            wrapper = lxml.html.Element('p')
            wrapper.append(ch)
            out.append(wrapper)


def flatten_layout_table(table, base, out):
    """Unwrap a layout table: each cell's content is flattened as if it were a div;
    loose text and inline elements in a cell are gathered into a <p>."""
    for td in table.xpath('.//td[not(.//td)]'):
        wrap = lxml.html.Element('div')
        para = None
        if td.text and td.text.strip():
            para = etree.SubElement(wrap, 'p')
            para.text = td.text
        for c in list(td):
            tag = c.tag.lower() if isinstance(c.tag, str) else ''
            if tag in BLOCK or tag in ('div', 'section', 'table'):
                wrap.append(c)
                para = None
            else:
                if para is None:
                    para = etree.SubElement(wrap, 'p')
                para.append(c)
        flatten(wrap, base, out)


def clean_inline(el, base):
    """Inner HTML of an element with junk tags/attributes stripped and links made absolute."""
    el = lxml.html.fromstring(lxml.html.tostring(el, encoding='unicode'))
    for bad in el.xpath('.//script|.//style|.//svg|.//button|.//noscript'):
        bad.drop_tree()
    for e in el.iter():
        if not isinstance(e.tag, str):
            continue
        if e.tag in ('span', 'font', 'div', 'picture', 'source'):
            e.drop_tag()
            continue
        for a in list(e.attrib):
            if a not in KEEP_ATTR:
                del e.attrib[a]
        if e.get('href'):
            e.set('href', urljoin(base, e.get('href')))
    inner = (html.escape(el.text) if el.text else '') + ''.join(
        lxml.html.tostring(c, encoding='unicode') for c in el)
    return inner.strip()


def to_html(node, base, heading_shift):
    tag = node.tag.lower()
    if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        lvl = min(4, max(2, int(tag[1]) + heading_shift))
        text = clean_inline(node, base)
        return f'<h{lvl}>{text}</h{lvl}>' if re.sub(r'<[^>]+>', '', text).strip() else ''
    if tag == 'hr':
        return '<hr>'
    if tag == 'pre':
        return '<pre>' + html.escape(node.text_content()) + '</pre>'
    attrs = ''
    if tag == 'ol' and node.get('start'):
        attrs = f' start="{node.get("start")}"'
    inner = clean_inline(node, base)
    if tag == 'p' and re.search(r'(<br\s*/?>\s*){2,}', inner):
        # <br><br>-separated "paragraphs" in one <p> (e.g. paulgraham.com) would be one unsplittable block
        paras = [x.strip() for x in re.split(r'(?:<br\s*/?>\s*){2,}', inner)]
        return '\n'.join(f'<p>{x}</p>' for x in paras if re.sub(r'<[^>]+>', '', x).strip())
    if not re.sub(r'<[^>]+>', '', inner).strip() and tag not in ('table',):
        return ''
    return f'<{tag}{attrs}>{inner}</{tag}>'


def extract(page, url):
    """Return (source, list of blocks). Prefer a known container when it holds
    most of the article text that trafilatura finds; else trafilatura's HTML."""
    traf = trafilatura.extract(page, url=url, output_format='html', include_images=True,
                               include_links=True, include_formatting=True, include_tables=True) or ''
    tw = words(traf)
    doc = lxml.html.fromstring(page)
    for name, xp in MARKERS:
        hits = doc.xpath(xp)
        if hits:
            el = max(hits, key=lambda e: len(e.text_content()))
            raw = lxml.html.tostring(el, encoding='unicode')
            if words(raw) >= 0.7 * tw and words(raw) > 150:
                out = []
                flatten(el, url, out)
                return name, out
    if not traf:
        return 'none', []
    out = []
    tree = lxml.html.fromstring(traf)
    body_el = tree.find('body')
    flatten(tree if body_el is None else body_el, url, out)
    return 'trafilatura', out


def slugify(url, title):
    host = urlparse(url).hostname or 'site'
    parts = [p for p in host.split('.') if p not in ('www', 'blog', 'newsletter', 'com', 'org', 'net', 'io', 'me', 'dev', 'co', 'substack', 'medium')]
    site = parts[-1] if parts else host.split('.')[0]
    if host.endswith('substack.com'):
        site = host.split('.')[0]
    path = [p for p in urlparse(url).path.split('/') if p]
    slug = re.sub(r'\.(html?|php|aspx?)$', '', path[-1]) if path else title
    slug = re.sub(r'[^a-z0-9]+', '-', slug.lower()).strip('-')
    if len(slug) < 6:
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return f'{site}-{slug[:50].strip("-")}'


def upload(pdf):
    folder = f'{DRIVE_DIR}/{date.today():%Y-%m-%d}'
    r = subprocess.run([RCLONE, 'copy', str(pdf), folder + '/'], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('rclone copy failed: ' + r.stderr.strip()[-400:])
    ls = subprocess.run([RCLONE, 'lsjson', f'{folder}/{pdf.name}'], capture_output=True, text=True)
    try:
        fid = json.loads(ls.stdout)[0]['ID']
        return f'https://drive.google.com/file/d/{fid}/view'
    except Exception:
        return None


def convert(url, upload_pdf=True, log=print, progress=lambda frac, stage: None):
    notes = []
    progress(0.02, 'Fetching page')
    status, final, page = fetch(url)
    if status >= 400 or not page.strip():
        raise RuntimeError(f'fetch failed: HTTP {status}')
    meta = trafilatura.extract_metadata(page, default_url=final)
    title = (meta.title if meta else None) or 'Untitled'
    author = (meta.author if meta else None) or ''
    published = (meta.date if meta else None) or ''
    progress(0.08, 'Extracting article')
    source, nodes = extract(page, final)
    log(f'extracted via {source}: {len(nodes)} blocks')
    if not nodes:
        raise RuntimeError('could not find article text on the page')

    # headings: the shallowest heading level in the body becomes h2
    levels = [int(n.tag[1]) for n in nodes if not isinstance(n, tuple) and re.fullmatch(r'h[1-6]', n.tag)]
    shift = 2 - min(levels) if levels else 0

    # trailing site chrome: "previous article", "share this", "tags:", short link-only blocks
    def chrome_tail(n):
        if isinstance(n, tuple):   # a repeat of an earlier image (series card, footer banner)
            return any(isinstance(m, tuple) and m[1] == n[1] for m in nodes[:-1])
        t = n.text_content()
        return len(t.split()) <= 8 and (bool(NAV_TAIL.search(t)) or not t.strip())
    while nodes and chrome_tail(nodes[-1]):
        nodes.pop()
    parts, dark, img_fail, seen = [], 0, 0, set()
    n_imgs, done_imgs = sum(isinstance(n, tuple) for n in nodes), 0
    for i, n in enumerate(nodes):
        if isinstance(n, tuple):
            done_imgs += 1
            progress(0.12 + 0.28 * done_imgs / max(1, n_imgs), f'Downloading images ({done_imgs}/{n_imgs})')
            if n[1] in seen:   # same image again (series cards, footer banners)
                continue
            seen.add(n[1])
            try:
                tag, im = image_tag(n[1], n[2])
            except Exception:
                img_fail += 1
                continue
            if im.width < 120 or im.height < 60:   # icons, avatars, tracking pixels
                continue
            dark += is_dark(im)
            parts.append(tag)
            continue
        h = to_html(n, final, shift)
        if i < 3 and re.sub(r'<[^>]+>', '', h).strip().lower() == title.strip().lower():
            continue   # repeated title inside the body
        if h:
            parts.append(h)
    body = '\n'.join(parts)
    for k, v in GLYPHS.items():
        body = body.replace(k, v)
    body = sidebyside(body)
    if dark:
        notes.append(f'{dark} image(s) have dark backgrounds and will print dark')
    if img_fail:
        notes.append(f'{img_fail} image(s) could not be downloaded')

    text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'</(p|h\d|li|blockquote|figcaption)>', '\n', body)))
    n_words = len(text.split())
    if n_words < 300:
        notes.append(f'only {n_words} words extracted — likely paywalled or an unusual layout; '
                     'no summary page added (try /blog-to-pdf in Claude Code)')
    else:
        try:
            progress(0.42, 'Writing summary page')
            s, model = summary.summarise(title, author, text, log=log)
            body_with = body + companion(s['tldr'], s['ideas'], s['actions'], by=f'Claude ({model})' if model.startswith('claude') else f'AI ({model})')
            body = body_with
        except Exception as e:
            notes.append(f'summary failed: {e}')
            log(f'summary failed: {e}')

    serif = site_font(page, final) == 'serif'
    fam = 'Gelasio:ital,wght@0,400;0,600;0,700;1,400' if serif else 'Inter:ital,wght@0,400;0,500;0,600;0,700;1,400'
    progress(0.78, 'Embedding fonts')
    fonts = google_fonts(fam + '&family=JetBrains+Mono:wght@400')
    face = 'Gelasio, Georgia, serif' if serif else 'Inter, system-ui, sans-serif'
    extra = f'''
.doc {{ font-family: {face}; font-size: {14.4 if serif else 13.8}px; line-height: 1.6; }}
.doc h1.title {{ text-align: left; font-size: 30px; line-height: 1.2; }}
.doc .meta, .doc .tags {{ text-align: left; }}
.doc h2 {{ font-size: 20px; margin: 26px 0 10px; }}
.doc h3 {{ font-size: 16px; margin: 20px 0 8px; }}
.doc h4 {{ font-size: 14px; margin: 16px 0 6px; }}
.doc code, .doc pre {{ font-family: "JetBrains Mono", monospace; font-size: 12px; }}
.doc pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f6f4; padding: 10px 12px; border-radius: 6px; }}
.doc table {{ border-collapse: collapse; font-size: 12px; }} .doc td, .doc th {{ border: 1px solid #ddd; padding: 4px 6px; }}
'''
    bits = [published, html.escape(author), f'~{max(1, round(n_words / 230))} min read', html.escape(urlparse(final).hostname or '')]
    meta_line = ' &middot; '.join(b for b in bits if b)

    name = slugify(final, title)
    OUT.mkdir(parents=True, exist_ok=True)
    doc_html, pdf = OUT / f'{name}.html', OUT / f'{name}.pdf'
    build(doc_html, html.escape(title), meta_line, [], body, fonts, extra)
    progress(0.84, 'Paginating')
    chk = dom_check(doc_html)
    if chk['error']:
        raise RuntimeError('paginator error: ' + chk['error'])
    if not chk['companion_fits'] and 'companion-title' in body:
        # trim the companion to 5+5 and rebuild once
        body = re.sub(r'(<ul>(?:<li>.*?</li>){5})(?:<li>.*?</li>)+(</ul><h3 class="cpn">)', r'\1\2', body, flags=re.S)
        body = re.sub(r'(<ul class="check">(?:<li>.*?</li>){5})(?:<li>.*?</li>)+(</ul>)', r'\1\2', body, flags=re.S)
        build(doc_html, html.escape(title), meta_line, [], body, fonts, extra)
        chk = dom_check(doc_html)
    progress(0.9, 'Rendering PDF')
    pages = render_pdf(doc_html, pdf)
    if pages != chk['sheets'] or os.path.getsize(pdf) < 15000:
        raise RuntimeError(f'render check failed: {pages} PDF pages vs {chk["sheets"]} sheets, {os.path.getsize(pdf)} bytes')
    if n_words > 300 and len(nodes) < 4:
        notes.append('extraction looked thin — check the PDF')
    if upload_pdf:
        progress(0.95, 'Uploading to Drive')
    link = upload(pdf) if upload_pdf and DRIVE_DIR else None
    return {'title': title, 'pages': pages, 'words': n_words, 'source': source, 'pdf': pdf.name,
            'html': doc_html.name, 'drive': link, 'notes': notes}


if __name__ == '__main__':
    print(json.dumps(convert(sys.argv[1], upload_pdf='--no-upload' not in sys.argv), indent=2))
