#!/usr/bin/env python3
"""Recreate Dario Amodei’s Scroll archive from saved original pages.

Requires beautifulsoup4. Run from the repository root. Downloads are optional;
the checked-in originals make conversion repeatable without network access.
"""
import argparse
import hashlib
import html
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString, Comment

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://darioamodei.com/'
assets = {}
failure_file = ROOT / 'originals/media-failures.json'
unavailable = {item['url'] for item in json.loads(failure_file.read_text())} if failure_file.exists() else set()

def clean(text):
    return re.sub(r'\s+', ' ', text).strip()

def esc(text):
    return html.escape(str(text), quote=True)

def indent(source, depth=1):
    return '\n'.join(' ' * depth + line for line in source.splitlines())

def element(cue='', text='', attrs=None, children=()):
    line = (cue + ' ' if cue and text else cue) + html.escape(str(text), quote=False)
    result = [line]
    for key, value in (attrs or {}).items():
        result.append(' ' + key + (' ' + esc(value) if str(value) else ''))
    result.extend(indent(child) for child in children)
    return '\n'.join(result)

def prose(text, *directives, **attrs):
    return element('', text, attrs, directives)

def link(text, url, **attrs):
    # A bare linked paragraph; use tag a + href only for cards containing children.
    cue = url if url.startswith(('http://', 'https://')) or url.endswith('.html') else 'link ' + url
    return prose(text, cue, **attrs)

def local_link(url):
    url = re.sub(r'#fn:(\d+)$', r'#footnote-\1', url)
    if url.startswith('#'): return url
    absolute = urljoin(current_url, re.sub(r'\s+', '', url))
    parsed = urlparse(absolute)
    slug = Path(parsed.path).stem
    if parsed.hostname in ('darioamodei.com','www.darioamodei.com') and slug in slugs:
        return slug + '.html' + ('#' + parsed.fragment if parsed.fragment else '')
    return absolute

def inline(node, directives):
    if isinstance(node, Comment): return ''
    if isinstance(node, NavigableString):
        return html.escape(str(node), quote=False)
    if node.name == 'anchorpoint':
        directives.append(('id', node['id']))
        return ''
    if node.name in ('script', 'style'):
        return ''
    if node.name == 'sup' and node.get_text(strip=True).isdigit():
        number = node.get_text(strip=True)
        label = '[' + number + ']'
        directives.append(('link #footnote-' + number, label))
        return label
    content = ''.join(inline(child, directives) for child in node.children)
    label = clean(content)
    if node.name == 'a':
        if node.get('href') and label:
            url = local_link(node['href'])
            cue = url if url.startswith(('http://', 'https://')) or re.search(r'\.html(?:#.*)?$', url) else 'link ' + url
            directives.append((cue, label))
        if node.get('id') or node.get('name'):
            directives.append(('id', node.get('id', node.get('name'))))
    cue = {'b': 'bold', 'strong': 'bold', 'i': 'italics', 'em': 'italics', 'u': 'underline', 'sup': 'superscript', 'sub': 'subscript', 'code': 'code'}.get(node.name)
    if cue and label:
        directives.append((cue, label))
    return content

BLOCKS = {'anchorpoint', 'p', 'div', 'ol', 'ul', 'li', 'blockquote', 'img', 'iframe', 'hr', 'h1', 'h2', 'h3', 'h4'}

def body_scroll(body, slug):
    lines = []
    def emit(nodes, prefix='', depth=0):
        directives = []
        text = clean(''.join(inline(node, directives) for node in nodes)).strip()
        if not text:
            return
        if not prefix and (text.startswith('free_feature') or re.match(r'^(?:[0-9]+[.)]|[-*])\s', text) or text.startswith(('http://', 'https://'))):
            prefix = 'h2' if ('bold', text) in directives else 'p'  # Escape command-like prose; promote whole bold numbered headings.
            if prefix == 'h2':
                directives.remove(('bold', text))
        # Bare prose uses Scroll's catchall; explicit cues are for structural elements.
        if re.fullmatch(r'h[1-4]', prefix):
            prefix = '#' * int(prefix[1])
        lines.append(' ' * depth + (prefix + ' ' if prefix else '') + text)
        if any(cue.startswith(('http', 'link ')) or '.html' in cue for cue, _ in directives) or re.search(r'https?://|www\.|@', text):
            lines.append(' ' * (depth + 1) + 'linkify false')
        for cue, label in dict.fromkeys(directives):
            selector = '' if label == text and cue != 'id' else ' ' + label
            lines.append(' ' * (depth + 1) + cue + selector)
        lines.append('')

    def walk(parent, depth=0, prefix=''):
        pending = []
        def flush():
            emit(pending, prefix, depth)
            pending.clear()
        for node in parent.children:
            if not isinstance(node, NavigableString) and node.name in ('script', 'style'):
                continue
            if not isinstance(node, NavigableString) and node.name == 'br':
                flush()
                continue
            if isinstance(node, NavigableString) or node.name not in BLOCKS:
                pending.append(node)
                continue
            flush()
            if node.name == 'anchorpoint':
                lines.extend(['span', ' id ' + node['id'], ''])
            elif node.name == 'img':
                url = urljoin(current_url, node.get('data-large-src') or node.get('src') or '')
                if not url:
                    continue
                ext = Path(urlparse(url).path).suffix.lower()
                if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
                    ext = '.jpg'
                filename = 'assets/' + hashlib.sha256(url.encode()).hexdigest()[:16] + ext
                assets[url] = filename
                if url in unavailable:
                    lines.extend(indent(element('', 'An image in the original post is no longer available. Original image link ↗', attrs={'addClass': 'unavailable-image'}, children=[url + ' Original image link ↗']), depth).splitlines())
                    continue
                lines.extend([' ' * depth + filename, ' ' * (depth + 1) + 'alt ' + (node.get('alt') or 'Image from ' + title_by_slug[slug]), ''])
            elif node.name == 'iframe':
                url = node.get('src', '').replace('http://', 'https://')
                lines.extend([' ' * depth + 'Read the embedded document.', ' ' * (depth + 1) + url, ''])
            elif node.name == 'hr':
                lines.extend([' ' * depth + '---', ''])
            elif node.name in ('ol', 'ul'):
                for number, item in enumerate(node.find_all('li', recursive=False), 1):
                    walk(item, depth, f'{number}.' if node.name == 'ol' else '-')
            elif node.name == 'blockquote':
                walk(node, depth, '>')
            else:
                walk(node, depth, node.name if node.name in ('li', 'h1', 'h2', 'h3', 'h4') else prefix)
        flush()
    walk(body)
    # An unindented blank closes a Scroll tree: never put one between list items.
    return '\n'.join(line for i, line in enumerate(lines) if line or (i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].startswith(' '))).strip()


def extract_body(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    body = soup.new_tag('div')
    for rich in list(soup.select('.w-richtext')):
        if 'cc-footnotes' in rich.get('class', []):
            for number, item in enumerate(rich.select('ol > li'), 1):
                marker = soup.new_tag('anchorpoint', id='footnote-' + str(number))
                item.insert_before(marker)
                # Use a normal paragraph with an explicit number so each note has an anchor.
                item.name = 'p'
                item.insert(0, '[' + str(number) + '] ')
            for listing in rich.select('ol'): listing.unwrap()
        body.append(rich.extract())
    for node in body.select('script,style'): node.decompose()
    return body

def fetch(url, dest):
    if dest.exists() and dest.stat().st_size: return
    subprocess.run(['curl','-fLsS','--retry','3','--max-time','90',url,'-o',str(dest)],check=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true', help='Download missing source pages')
    args = parser.parse_args()
    if args.fetch: fetch(BASE, ROOT / 'originals/index.html')
    home = BeautifulSoup((ROOT / 'originals/index.html').read_text(), 'html.parser')
    paths = list(dict.fromkeys(a['href'] for a in home.select('a[href]') if a['href'].startswith(('/essay/', '/post/'))))
    slugs = {path.rsplit('/', 1)[-1] for path in paths}
    title_by_slug = {}
    posts = []
    for path in paths:
        slug = path.rsplit('/', 1)[-1]
        current_url = urljoin(BASE, path)
        original = ROOT / 'originals' / (slug + '.html')
        if args.fetch: fetch(current_url, original)
        raw = original.read_text()
        soup = BeautifulSoup(raw, 'html.parser')
        title = soup.select_one('meta[property="og:title"]')['content'].split('—', 1)[-1].strip()
        title_by_slug[slug] = title
        date_label = soup.select_one('.post-date').get_text(strip=True)
        date = datetime.strptime(date_label, '%B %Y').strftime('%Y-%m-%d')
        subtitle_node = soup.select_one('.post-subtitle')
        subtitle = subtitle_node.get_text(strip=True) if subtitle_node else ''
        body = extract_body(raw)
        summary = clean(body.get_text(' ', strip=True))[:160]
        source = f'title {esc(title)}\ndescription {esc(subtitle or summary)}\ndate {date}\ncanonicalUrl {current_url}\n\npost-header.scroll\n\n'
        if subtitle: source += esc(subtitle) + '\n italics\n\n'
        source += body_scroll(body, slug) + '\n\nfooter.scroll\n'
        (ROOT / (slug + '.scroll')).write_text(source)
        posts.append(dict(title=title, slug=slug, url=current_url, date=date, publicationLabel=date_label, subtitle=subtitle, scrollFile=slug+'.scroll', original=slug+'.html'))
    (ROOT / 'archive.json').write_text(json.dumps(posts, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(posts)} articles.')
