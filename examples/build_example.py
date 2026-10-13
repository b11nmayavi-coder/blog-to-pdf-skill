#!/usr/bin/env python3
"""Example build script: a Substack post -> print-ready A4 PDF.

Run from a scratch directory:  python3 build_example.py <url>
Adapt the extraction section per site; everything else is generic.
"""
import html, re, sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent      # or the path printed when the skill loads
sys.path.insert(0, str(SKILL_DIR))
from helpers import (fetch, container, blocks, google_fonts, image_tag, is_dark,
                     sidebyside, odd_glyphs, companion, build, render_pdf, page_grid)

url = sys.argv[1]
page = fetch(url).decode('utf-8', 'replace')
Path('dl').mkdir(exist_ok=True)
Path('dl/page.html').write_text(page)

# ---- header metadata ----------------------------------------------------
title = re.sub(r'<[^>]+>', '', re.search(r'<h1[^>]*class="[^"]*post-title[^"]*"[^>]*>(.*?)</h1>', page, re.S).group(1))
m = re.search(r'<h3[^>]*class="[^"]*subtitle[^"]*"[^>]*>(.*?)</h3>', page, re.S)
subtitle = re.sub(r'<[^>]+>', '', m.group(1)) if m else ''
author = (re.search(r'<meta name="author" content="([^"]*)"', page) or [None, ''])[1]

# ---- article body -> flat blocks ----------------------------------------
inner = container(page, 'class="body markup"')


def clean_inline(x):
    x = re.sub(r'</?span[^>]*>', '', x)
    x = re.sub(r'<a [^>]*?href="([^"]*)"[^>]*>', r'<a href="\1">', x)
    x = re.sub(r'\s*(class|data-[a-z-]+|id|target|rel)="[^"]*"', '', x)
    return x.replace('⁠', '').replace(' ', ' ')


out, dark = [], []
for b in blocks(inner):
    head = b[:80]
    if 'captioned-image-container' in head:
        src = re.search(r'<img src="([^"]*)"', b).group(1)
        cap = re.search(r'<figcaption[^>]*>(.*?)</figcaption>', b, re.S)
        cap = clean_inline(cap.group(1)).strip() if cap else ''
        tag, im = image_tag(src, cap)
        if is_dark(im):
            dark.append(cap[:50] or src[-40:])
        out.append(tag)
        continue
    if 'youtube-wrap' in head or 'subscription-widget' in head:
        continue
    if 'button-wrapper' in head:                      # "Watch the talk" style buttons -> link paragraph
        href = re.search(r'href="([^"]*)"', b).group(1)
        text = re.sub(r'<[^>]+>', '', b).strip()
        out.append(f'<p class="cta">{text}: <a href="{href}">{href}</a></p>')
        continue
    x = clean_inline(b)
    x = re.sub(r'<h([23])[^>]*>', r'<h\1>', x)
    x = re.sub(r'(<h[23]>.*?)<div.*?</h([23])>', r'\1</h\2>', x, flags=re.S)   # Substack anchor buttons
    x = re.sub(r'<(p|blockquote|ol|ul|li)\s+[^>]*>', r'<\1>', x)
    out.append(x)

body = sidebyside('\n'.join(out))
print('odd glyphs:', odd_glyphs(body))
print('dark images:', dark)

text = html.unescape(re.sub(r'<[^>]+>', '', re.sub(r'</(p|h2|h3|li|blockquote|figcaption)>', '\n', body)))
Path('article.txt').write_text(text)                  # READ THIS before writing the companion
words = len(text.split())

# ---- companion (write these from the full text, keep to one page) ------
tldr = 'TL;DR goes here.'
ideas = ['Key idea 1', 'Key idea 2']
actions = ['Action 1', 'Action 2']
body += companion(tldr, ideas, actions)

# ---- typography & build ----------------------------------------------
fonts = google_fonts('Inter:wght@400;500;600;700&family=Lora:wght@600;700')
extra = '''
.doc { font-family: Inter, system-ui, sans-serif; font-size: 13.6px; line-height: 1.58; }
.doc h1.title, .doc h2, .doc h3, .doc .companion-title { font-family: Lora, Georgia, serif; }
.doc h1.title { text-align: left; font-size: 30px; line-height: 1.2; }
.doc .subtitle { color: #5c5a53; font-size: 15px; line-height: 1.45; margin: 0 0 10px; }
.doc .meta, .doc .tags { text-align: left; }
.doc h2 { font-size: 22px; margin: 26px 0 10px; }
.doc h3 { font-size: 16px; font-weight: 700; margin: 20px 0 8px; }
.doc .cta { background: #f7f6f2; border: 1px solid #e6e4de; border-radius: 6px; padding: 9px 13px; }
.doc blockquote p + p { margin-top: 8px; }
'''
meta = f'{author} &middot; ~{max(1, round(words / 230))} min read'
sub = f'<p class="subtitle">{subtitle}</p>' if subtitle else ''
build('doc.html', title, meta, [], sub + body, fonts, extra)
render_pdf('doc.html', 'out.pdf')
page_grid('out.pdf', 'grid.png')                      # LOOK at grid.png before delivering
