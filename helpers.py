#!/usr/bin/env python3
"""Reusable helpers for the blog-to-pdf skill.

Import from a small per-article build script. SKILL_DIR is the directory this
file lives in (the "base directory" the harness prints when the skill loads):

    import sys; sys.path.insert(0, SKILL_DIR)
    from helpers import *

Only the Python standard library + Pillow are required (no bs4/lxml).
"""
import base64, glob, html as _html, io, os, re, shutil, subprocess
from pathlib import Path
from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36'

VOID = {'img', 'br', 'hr', 'source', 'input', 'meta', 'link', 'wbr', 'col',
        'embed', 'area', 'base', 'param', 'track'}


def fetch(url):
    """Download a URL with a browser-like User-Agent and return the bytes."""
    return subprocess.run(['curl', '-sL', '-A', UA, url], capture_output=True).stdout


def container(html, start_marker):
    """Return the inner HTML of the first element whose opening tag contains
    start_marker (e.g. 'class="body markup"' or '<article'), found by tag
    balancing on <div>/<article>/<section> — no HTML parser needed."""
    j = html.find(start_marker)
    if j < 0:
        raise ValueError(f'marker not found: {start_marker}')
    j = html.rfind('<', 0, j + 1)
    tag = re.match(r'<([a-zA-Z0-9]+)', html[j:]).group(1).lower()
    i = html.find('>', j) + 1
    depth = 1
    for m in re.finditer(rf'<(/?){tag}\b[^>]*>', html[i:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[i:i + m.start()]
    return html[i:]


def blocks(inner):
    """Split an HTML string into its top-level element strings (the atomic
    blocks the paginator works with). Loose text between elements is kept as
    its own entry so nothing is silently dropped."""
    out, depth, start, pos = [], 0, None, 0
    for m in re.finditer(r'<(/?)([a-zA-Z0-9]+)[^>]*?(/?)>', inner):
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if depth == 0 and not closing:
            start = m.start()
            txt = inner[pos:start].strip()
            if txt:
                out.append(txt)
        if closing:
            depth -= 1
        elif tag in VOID or selfclose:
            pass
        else:
            depth += 1
        if depth == 0 and start is not None:
            out.append(inner[start:m.end()])
            pos, start = m.end(), None
    return out


def google_fonts(spec):
    """Fetch a Google Fonts family and return @font-face CSS with the latin-subset
    woff2 files embedded as data URIs (so the document is self-contained and
    works under a strict CSP).

    spec examples: 'Inter:wght@400;500;600;700'
                   'Gelasio:ital,wght@0,400;0,700;1,400'
    Chain multiple families with '&family=' inside one spec string.
    """
    url = f'https://fonts.googleapis.com/css2?family={spec}&display=swap'
    css = subprocess.run(['curl', '-sL', '-A', UA, url], capture_output=True, text=True).stdout
    faces = []
    for subset, face in re.findall(r'/\* (\S+) \*/\s*(@font-face\s*\{[^}]+\})', css):
        if subset != 'latin':
            continue
        u = re.search(r'url\((https://[^)]+)\)', face).group(1)
        woff = subprocess.run(['curl', '-sL', u], capture_output=True).stdout
        b64 = base64.b64encode(woff).decode()
        faces.append(re.sub(r'url\(https://[^)]+\)', f'url(data:font/woff2;base64,{b64})', face))
    return '\n'.join(faces)


def load_image(src):
    """URL or local path -> PIL Image (RGB)."""
    if re.match(r'https?://', str(src)):
        im = Image.open(io.BytesIO(fetch(src)))
    else:
        im = Image.open(src)
    return im.convert('RGB')


def img_uri(src, max_w=1200, photo=False):
    """URL, local path or PIL Image -> optimized data URI.
    photo=False quantizes to 256-color PNG (flat-color diagrams shrink ~4x);
    photo=True re-encodes as JPEG q82 (photographs, covers)."""
    im = src if isinstance(src, Image.Image) else load_image(src)
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    if photo:
        im.convert('RGB').save(buf, 'JPEG', quality=82)
        mime = 'jpeg'
    else:
        im.convert('RGB').quantize(colors=256, method=Image.MEDIANCUT).save(buf, 'PNG', optimize=True)
        mime = 'png'
    return f'data:image/{mime};base64,' + base64.b64encode(buf.getvalue()).decode()


def is_dark(im):
    """True when an image has a dark background (mean luminance < 90/255).
    Dark-theme diagrams print badly; look for a light variant or warn the user."""
    return sum(im.resize((64, 64)).convert('L').getdata()) / 4096 < 90


def image_tag(src, caption='', photo=None, portrait_below=1.2):
    """Build the <img> the paginator and sidebyside() expect:
    data-orient from real dimensions (aspect < portrait_below counts as
    portrait, so near-square charts also share a page with text) and an
    optional data-cap caption. photo=None guesses from the file extension."""
    im = load_image(src)
    if photo is None:
        photo = bool(re.search(r'\.jpe?g', str(src), re.I))
    orient = 'portrait' if im.width / im.height < portrait_below else 'landscape'
    cap = _html.escape(caption, quote=True)   # caption may contain HTML; keep the <img> tag intact
    return f'<img src="{img_uri(im, photo=photo)}" data-orient="{orient}" data-cap="{cap}">', im


def sidebyside(body, char_budget=800):
    """Lay out images. Portrait-tagged images share a page with the paragraphs
    that follow them (a tall diagram beside the text discussing it, instead of
    eating a full page); landscape images become full-width figures. Images
    must carry data-orient="portrait"/"landscape"; an optional data-cap
    attribute becomes a <figcaption>."""
    parts = re.split(r'(<img [^>]*>)', body)
    out = []
    for k, part in enumerate(parts):
        if not part.startswith('<img'):
            out.append(part)
            continue
        m = re.search(r'\s*data-cap="([^"]*)"', part)
        cap = _html.unescape(m.group(1)) if m else ''
        img = part.replace(m.group(0), '') if m else part
        caption = f'<figcaption>{cap}</figcaption>' if cap else ''
        if 'data-orient="landscape"' in img:
            out.append(f'<figure>{img}{caption}</figure>')
            continue
        rest = parts[k + 1] if k + 1 < len(parts) else ''
        pulled, budget = [], char_budget
        while budget > 0:
            pm = re.match(r'\s*<p>(.*?)</p>', rest, re.S)
            if not pm:
                break
            txt = re.sub(r'<[^>]+>', '', pm.group(1))
            if len(txt) > budget + 260:
                break
            pulled.append(pm.group(0))
            budget -= len(txt)
            rest = rest[pm.end():]
        if k + 1 < len(parts):
            parts[k + 1] = rest
        if pulled:
            out.append(f'<div class="figrow"><figure class="side">{img}{caption}</figure>'
                       f'<div class="figside">{"".join(pulled)}</div></div>')
        else:
            out.append(f'<figure class="solo">{img}{caption}</figure>')
    return ''.join(out)


def odd_glyphs(body):
    """Characters outside the Google Fonts 'latin' subset (they would render as
    tofu). Rewrite them in prose before building."""
    text = re.sub(r'<img [^>]*>', '', body)
    return sorted(set(c for c in text if ord(c) > 0x24F and not 0x2000 <= ord(c) <= 0x206F))


def companion(tldr, ideas, actions):
    """One-page 'Reader's companion' appended after the article: TL;DR, key
    ideas, and checkbox action points. Clearly labeled as Claude's addition."""
    return (
        '<p class="crumb break-page">Reader’s companion · summary &amp; action points · '
        'added by Claude — not part of the original article</p>'
        '<h2 class="companion-title">Summary &amp; action points</h2>'
        f'<p>{tldr}</p>'
        '<h3 class="cpn">Key ideas</h3><ul>' +
        ''.join(f'<li>{x}</li>' for x in ideas) +
        '</ul><h3 class="cpn">Action points</h3><ul class="check">' +
        ''.join(f'<li>{x}</li>' for x in actions) + '</ul>')


def build(out_path, title, meta, chips, body, fonts_css, extra_css=''):
    """Assemble the self-paginating print document and write it to out_path."""
    tpl = (SKILL_DIR / 'print_template.html').read_text(encoding='utf-8')
    head = f'<h1 class="title">{title}</h1><p class="meta">{meta}</p>'
    if chips:
        head += '<p class="tags">' + ' '.join(f'<span class="chip">{c}</span>' for c in chips) + '</p>'
    doc = (tpl.replace('{{TITLE}}', title)
              .replace('/*FONTS*/', fonts_css)
              .replace('/*EXTRA*/', extra_css)
              .replace('<!--ARTICLE-->', head + body))
    Path(out_path).write_text(doc, encoding='utf-8')
    print(f'{out_path}: {len(doc)/1e6:.1f} MB')


def chrome_bin():
    for c in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome',
              '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'):
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return p
    raise RuntimeError('Chrome/Chromium not found on PATH')


def render_pdf(html_path, pdf_path):
    """Print the document to PDF with headless Chrome and return the page count.
    A tiny PDF with a plausible page count means pagination never ran."""
    subprocess.run([chrome_bin(), '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                    '--run-all-compositor-stages-before-draw', '--virtual-time-budget=30000',
                    f'--print-to-pdf={pdf_path}', f'file://{Path(html_path).resolve()}'],
                   capture_output=True)
    info = subprocess.run(['pdfinfo', pdf_path], capture_output=True, text=True).stdout
    pages = int((re.search(r'Pages:\s+(\d+)', info) or [0, 0])[1])
    print(f'{pdf_path}: {os.path.getsize(pdf_path)/1e6:.1f} MB, {pages} pages')
    return pages


def page_grid(pdf_path, out_png, cols=6, dpi=40):
    """Rasterize every page and compose a contact sheet for visual QA
    (stranded headings, half-empty pages, cut images, companion overflow)."""
    tmp = Path(out_png).with_suffix('')
    for f in glob.glob(f'{tmp}-*.png'):
        os.remove(f)
    subprocess.run(['pdftoppm', '-png', '-r', str(dpi), pdf_path, str(tmp)], check=True)
    files = sorted(glob.glob(f'{tmp}-*.png'))
    ims = [Image.open(f) for f in files]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    g = Image.new('RGB', (cols * (w + 8), rows * (h + 8)), '#888')
    for i, im in enumerate(ims):
        g.paste(im, ((i % cols) * (w + 8), (i // cols) * (h + 8)))
    g.save(out_png)
    for f in files:
        os.remove(f)
    return out_png
