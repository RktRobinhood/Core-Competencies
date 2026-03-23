#!/usr/bin/env python3
"""
Nightly build script: Converts Obsidian vault markdown files to a hosted wiki website.

Usage:
  python3 build.py                # full rebuild
  python3 build.py --incremental  # only rebuild changed files

Output: vault/docs/  (flat HTML directory + style.css)
GitHub Pages: point to the /docs folder on main branch.
A .nojekyll file is written automatically so GitHub skips Jekyll.
"""

import os
import re
import json
import sys
import shutil
import hashlib
import markdown as md_lib
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
VAULT_ROOT   = SCRIPT_DIR.parent          # .../Core/
SITE_DIR     = VAULT_ROOT          # GitHub Pages serves from repo root
STYLE_SRC    = SCRIPT_DIR / 'style.css'
CACHE_FILE   = SCRIPT_DIR / '.build-cache.json'
TOC_FILE     = VAULT_ROOT / '00-Table-of-Contents.md'

EXCLUDE_DIRS  = {'docs', '_site', '_build', '_archive', '_templates', '.obsidian', '.git'}
EXCLUDE_FILES = {'To-Do-List.md', 'Completed-Log.md'}

SITE_TITLE    = "Developing Learners, Developing Individuals"
SITE_SUBTITLE = "A Research Foundation for Expanding IB Theory of Knowledge"

# ── Callout type → (icon, css-class) ─────────────────────────────────────────
CALLOUT_MAP = {
    'abstract': ('◉', 'abstract'), 'summary': ('◉', 'abstract'),
    'info':     ('ℹ', 'info'),     'note':    ('✎', 'note'),
    'tip':      ('◆', 'tip'),      'hint':    ('◆', 'tip'),
    'important':('★', 'important'),
    'success':  ('✔', 'success'),  'check':   ('✔', 'success'), 'done': ('✔', 'success'),
    'question': ('?', 'question'), 'help':    ('?', 'question'), 'faq': ('?', 'question'),
    'warning':  ('⚠', 'warning'),  'caution': ('⚠', 'warning'), 'attention': ('⚠', 'warning'),
    'failure':  ('✘', 'failure'),  'fail':    ('✘', 'failure'), 'missing': ('✘', 'failure'),
    'danger':   ('☠', 'danger'),   'error':   ('☠', 'danger'),
    'bug':      ('⚙', 'bug'),
    'example':  ('»', 'example'),
    'quote':    ('"', 'quote'),    'cite':    ('"', 'quote'),
}


# ── Frontmatter ───────────────────────────────────────────────────────────────
def parse_frontmatter(text):
    """Return (meta_dict, body_text)."""
    try:
        import yaml
    except ImportError:
        return {}, text

    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
                return meta, text[end + 5:]
            except Exception:
                pass
    return {}, text


# ── Callout conversion ────────────────────────────────────────────────────────
def convert_callouts(text):
    """Transform Obsidian callout blocks to HTML divs."""
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        m = re.match(r'^> \[!(\w+)\]\s*(.*)$', line)
        if m:
            raw_type  = m.group(1).lower()
            title_txt = m.group(2).strip()
            icon, css_class = CALLOUT_MAP.get(raw_type, ('◉', 'note'))

            body_lines = []
            i += 1
            while i < len(lines) and (lines[i].startswith('> ') or lines[i] == '>'):
                body_lines.append(lines[i][2:] if lines[i].startswith('> ') else '')
                i += 1

            body_md   = '\n'.join(body_lines)
            body_html = _md(body_md)

            title_display = title_txt if title_txt else raw_type.capitalize()
            html = (
                f'\n<div class="callout callout-{css_class}">'
                f'<div class="callout-title"><span class="callout-icon">{icon}</span>'
                f' {title_display}</div>'
                f'<div class="callout-body">{body_html}</div>'
                f'</div>\n'
            )
            result.append(html)
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


# ── Wikilink conversion ───────────────────────────────────────────────────────
def convert_wikilinks(text):
    """[[Target|Display]] and [[Target]] → HTML anchor tags."""
    text = re.sub(
        r'\[\[([^\]|#]+?)(?:#[^\]|]*)?\|([^\]]+)\]\]',
        lambda m: f'<a href="{_href(m.group(1))}">{m.group(2).strip()}</a>',
        text
    )
    text = re.sub(
        r'\[\[([^\]|#]+?)(?:#[^\]]*?)?\]\]',
        lambda m: f'<a href="{_href(m.group(1))}">{m.group(1).strip()}</a>',
        text
    )
    return text


def _href(stem):
    stem = stem.strip()
    stem = Path(stem).name
    return f'{stem}.html'


# ── Markdown conversion ───────────────────────────────────────────────────────
def _md(text):
    """Convert plain markdown to HTML (new instance each call)."""
    converter = md_lib.Markdown(extensions=['extra', 'tables', 'toc'])
    return converter.convert(text)


def convert_file(md_text):
    """Full pipeline: frontmatter → callouts → wikilinks → markdown → HTML body."""
    meta, body = parse_frontmatter(md_text)
    body = convert_callouts(body)
    body = convert_wikilinks(body)
    html_body = _md(body)
    return meta, html_body


# ── Search index ──────────────────────────────────────────────────────────────
def md_to_plaintext(body):
    """Strip markdown syntax to produce clean searchable plain text."""
    t = body
    # Remove callout markers (> [!type] Title)
    t = re.sub(r'^> \[!\w+\].*$', '', t, flags=re.MULTILINE)
    # Strip blockquote prefix
    t = re.sub(r'^>\s?', '', t, flags=re.MULTILINE)
    # Wikilinks [[Target|Display]] → Display, [[Target]] → Target
    t = re.sub(r'\[\[[^\]|]+\|([^\]]+)\]\]', r'\1', t)
    t = re.sub(r'\[\[([^\]]+)\]\]', r'\1', t)
    # Strip headings markers
    t = re.sub(r'^#{1,6}\s+', '', t, flags=re.MULTILINE)
    # Strip bold/italic markers
    t = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', t)
    t = re.sub(r'_{1,3}([^_\n]+)_{1,3}', r'\1', t)
    # Strip horizontal rules
    t = re.sub(r'^---+$', '', t, flags=re.MULTILINE)
    # Strip inline code backticks
    t = re.sub(r'`[^`]+`', '', t)
    # Strip YAML-looking lines
    t = re.sub(r'^\w[\w\s]*:.*$', '', t, flags=re.MULTILINE)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def toc_slugify(text):
    """Replicate python-markdown TOC extension's slug generation for heading IDs."""
    import unicodedata
    value = unicodedata.normalize('NFKD', text)
    value = value.encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value


def extract_sections(body):
    """
    Split markdown body into sections by H2/H3 headings.
    Returns list of {id, heading, text} matching the IDs python-markdown generates.
    """
    sections = []
    current_heading = None
    current_id      = None
    current_lines   = []

    for line in body.split('\n'):
        m = re.match(r'^(#{2,3})\s+(.+)$', line)
        if m:
            if current_heading is not None:
                plain = md_to_plaintext('\n'.join(current_lines))
                if plain:
                    sections.append({'id': current_id, 'heading': current_heading, 'text': plain})
            current_heading = m.group(2).strip()
            current_id      = toc_slugify(current_heading)
            current_lines   = []
        else:
            current_lines.append(line)

    # flush last section
    if current_heading is not None:
        plain = md_to_plaintext('\n'.join(current_lines))
        if plain:
            sections.append({'id': current_id, 'heading': current_heading, 'text': plain})

    return sections


def build_search_index(md_files):
    """
    Build a full-text search index from every markdown file.
    Each entry has frontmatter metadata + per-section data so search results
    can link directly to the section where the match was found.
    Writes docs/search-index.js as a synchronous JS assignment (works with file://).
    """
    index = []
    for md_path in md_files:
        text = md_path.read_text(encoding='utf-8')
        meta, body = parse_frontmatter(text)

        stem  = md_path.stem
        title = meta.get('title') or stem.replace('-', ' ').replace('_', ' ')
        tags  = meta.get('tags') or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',')]

        entry = {
            'href':     f'{stem}.html',
            'title':    title,
            'part':     meta.get('part', ''),
            'chapter':  meta.get('chapter', ''),
            'summary':  meta.get('summary', ''),
            'tags':     [str(t) for t in tags],
            'sections': extract_sections(body),
        }
        index.append(entry)

    payload = json.dumps(index, ensure_ascii=False, separators=(',', ':'))
    js_out  = SITE_DIR / 'search-index.js'
    js_out.write_text('window.SEARCH_INDEX=' + payload + ';', encoding='utf-8')
    return index


# ── Sidebar ───────────────────────────────────────────────────────────────────
def build_sidebar(active_stem=None):
    """Parse 00-Table-of-Contents.md to generate sidebar HTML."""
    if not TOC_FILE.exists():
        return '<nav class="sidebar-nav"><p>No TOC found.</p></nav>'

    toc_text = TOC_FILE.read_text(encoding='utf-8')
    _, body  = parse_frontmatter(toc_text)

    parts   = []
    current = None

    for line in body.split('\n'):
        h2 = re.match(r'^## (.+)$', line)
        if h2:
            current = {'title': h2.group(1).strip(), 'items': []}
            parts.append(current)
            continue

        li = re.match(r'^[-*] \[\[([^\]|]+?)(?:\|([^\]]+))?\]\]', line)
        if li and current is not None:
            stem  = li.group(1).strip()
            label = li.group(2).strip() if li.group(2) else stem
            href  = _href(stem)
            current['items'].append((href, label))

    html = ['<nav class="sidebar-nav" id="sidebarNav">']

    # Brand
    html.append(
        f'<a class="sidebar-brand" href="00-Table-of-Contents.html">'
        f'<span>{SITE_TITLE}</span></a>'
    )

    # Search box
    html.append(
        '<div class="sidebar-search">'
        '<input type="search" id="sidebarSearch" placeholder="Search chapters…" autocomplete="off" spellcheck="false">'
        '</div>'
    )

    # Search results (hidden until query entered)
    html.append('<ul class="search-results" id="searchResults" hidden></ul>')

    # Part groups (shown when not searching)
    html.append('<div id="sidebarParts">')
    for part in parts:
        has_active = any(
            (active_stem and Path(href).stem == active_stem)
            for href, _ in part['items']
        )
        open_attr = ' open' if has_active else ''

        html.append(f'<details class="part-group"{open_attr}>')
        html.append(f'  <summary class="part-title">{part["title"]}</summary>')
        html.append(f'  <ul class="chapter-list">')

        for href, label in part['items']:
            stem = Path(href).stem
            active_cls = ' class="active"' if (active_stem and stem == active_stem) else ''
            html.append(f'    <li><a href="{href}"{active_cls}>{label}</a></li>')

        html.append(f'  </ul>')
        html.append(f'</details>')
    html.append('</div>')  # #sidebarParts

    # Inline section nav injected by JS under the active chapter <li>
    # (no static placeholder needed — JS creates it at runtime)

    html.append('</nav>')
    return '\n'.join(html)


# ── HTML template ─────────────────────────────────────────────────────────────
# Uses __PLACEHOLDER__ tokens to avoid Python f-string brace conflicts with JS.
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__PAGE_TITLE__ — __SITE_TITLE__</title>
  <link rel="stylesheet" href="style.css">
  <script src="search-index.js"></script>
</head>
<body>

<!-- Mobile hamburger -->
<button class="sidebar-toggle" id="sidebarToggle" aria-label="Open navigation">
  <span></span><span></span><span></span>
</button>

<!-- Desktop sidebar collapse tab -->
<button class="sidebar-collapser" id="sidebarCollapser" aria-label="Toggle sidebar">
  <span class="collapser-icon">&#8249;</span>
</button>

<div class="layout" id="layout">
  <aside class="sidebar" id="sidebar">
    __SIDEBAR__
  </aside>

  <main class="content" id="main">
    <article>
      __BREADCRUMB__
      __CONTENT__
    </article>
    <footer class="page-footer">
      <p>Part of <em>__SITE_TITLE__</em> · __SITE_SUBTITLE__</p>
      <p class="build-time">Last built: __BUILD_TIME__</p>
    </footer>
  </main>
</div>

<script>
(function () {
  'use strict';

  // ── Desktop collapse ───────────────────────────────────────────────────────
  const layout    = document.getElementById('layout');
  const collapser = document.getElementById('sidebarCollapser');
  const sidebar   = document.getElementById('sidebar');
  const mobToggle = document.getElementById('sidebarToggle');
  const SK_DESK   = 'sidebar-desktop-open';

  function setDesktopSidebar(open) {
    layout.classList.toggle('sidebar-collapsed', !open);
    collapser.querySelector('.collapser-icon').innerHTML = open ? '&#8249;' : '&#8250;';
    collapser.setAttribute('aria-label', open ? 'Collapse sidebar' : 'Expand sidebar');
    try { sessionStorage.setItem(SK_DESK, open ? '1' : '0'); } catch(e) {}
  }

  collapser.addEventListener('click', () => setDesktopSidebar(layout.classList.contains('sidebar-collapsed')));

  try { setDesktopSidebar(sessionStorage.getItem(SK_DESK) !== '0'); }
  catch(e) { setDesktopSidebar(true); }

  // ── Mobile sidebar ─────────────────────────────────────────────────────────
  mobToggle.addEventListener('click', () => {
    const open = !sidebar.classList.contains('mobile-open');
    sidebar.classList.toggle('mobile-open', open);
    mobToggle.classList.toggle('active', open);
  });

  document.addEventListener('click', (e) => {
    if (window.innerWidth < 900 && sidebar.classList.contains('mobile-open')
        && !sidebar.contains(e.target) && e.target !== mobToggle) {
      sidebar.classList.remove('mobile-open');
      mobToggle.classList.remove('active');
    }
  });

  // ── Search (full-text across all chapters) ─────────────────────────────────
  // Index is loaded synchronously via <script src="search-index.js"> in <head>,
  // so window.SEARCH_INDEX is always available — no fetch, works with file://
  const searchInput   = document.getElementById('sidebarSearch');
  const searchResults = document.getElementById('searchResults');
  const partsDiv      = document.getElementById('sidebarParts');

  function highlight(text, query) {
    if (!query || !text) return text || '';
    const lower = text.toLowerCase();
    const qLow  = query.toLowerCase();
    const qLen  = query.length;
    let result = '';
    let i = 0;
    while (i < text.length) {
      const idx = lower.indexOf(qLow, i);
      if (idx === -1) { result += text.slice(i); break; }
      result += text.slice(i, idx) + '<mark>' + text.slice(idx, idx + qLen) + '</mark>';
      i = idx + qLen;
    }
    return result;
  }

  function excerpt(body, query) {
    if (!body || !query) return '';
    const idx = body.toLowerCase().indexOf(query.toLowerCase());
    if (idx === -1) return '';
    const start = Math.max(0, idx - 40);
    const end   = Math.min(body.length, idx + 100);
    return (start > 0 ? '…' : '') + highlight(body.slice(start, end), query) + '…';
  }

  // Find the first section in an item whose text or heading contains the query word
  function findSection(item, word) {
    if (!item.sections) return null;
    return item.sections.find(function(s) {
      return s.text.toLowerCase().indexOf(word) !== -1
          || s.heading.toLowerCase().indexOf(word) !== -1;
    }) || null;
  }

  // Build a deep link: page.html?q=term#section-id
  function deepLink(item, q, section) {
    const qEnc = encodeURIComponent(q);
    return item.href + '?q=' + qEnc + (section ? '#' + section.id : '');
  }

  function renderResults(query) {
    const q = query.trim();
    if (!q) {
      searchResults.hidden = true;
      partsDiv.hidden = false;
      return;
    }
    partsDiv.hidden = true;
    searchResults.hidden = false;

    const index = window.SEARCH_INDEX || [];
    const words = q.toLowerCase().split(' ').filter(function(w) { return w.length > 0; });

    const hits = index.filter(function(item) {
      const sectionText = (item.sections || []).map(function(s) {
        return s.heading + ' ' + s.text;
      }).join(' ');
      const blob = [item.title, item.summary, item.part,
                    (item.tags || []).join(' '), sectionText].join(' ').toLowerCase();
      return words.every(function(w) { return blob.indexOf(w) !== -1; });
    }).slice(0, 15);

    if (hits.length === 0) {
      searchResults.innerHTML = '<li class="search-no-results">No results found</li>';
      return;
    }

    const firstWord = words[0];
    searchResults.innerHTML = hits.map(function(item) {
      const section   = findSection(item, firstWord);
      const href      = deepLink(item, q, section);
      const titleHtml = highlight(item.title, q);
      const tags = (item.tags || []).slice(0, 3)
        .map(function(t) { return '<span class="search-tag">' + t + '</span>'; }).join('');

      // Snippet: prefer section heading + excerpt, fall back to summary
      let snip = '';
      if (section) {
        const secLabel = '<span class="search-section">§ ' + highlight(section.heading, q) + '</span>';
        const inHeading = section.heading.toLowerCase().indexOf(firstWord) !== -1;
        snip = secLabel + (inHeading ? '' : '<span class="search-summary">' + excerpt(section.text, firstWord) + '</span>');
      } else if (item.summary) {
        snip = '<span class="search-summary">' + highlight(item.summary.slice(0, 110), q) + '…</span>';
      }

      return '<li><a href="' + href + '"><span class="search-title">' + titleHtml + '</span>' + tags + snip + '</a></li>';
    }).join('');
  }

  if (searchInput) {
    searchInput.addEventListener('input', function(e) { renderResults(e.target.value); });
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { searchInput.value = ''; renderResults(''); searchInput.blur(); }
    });
  }

  // ── Arrival highlight: light up matched text when coming from search ──────
  (function arrivalHighlight() {
    const q = new URLSearchParams(location.search).get('q');
    if (!q) return;

    const article = document.querySelector('article');
    if (!article) return;

    const marks = [];

    function walkNode(node) {
      if (node.nodeType === 3) {
        const text  = node.textContent;
        const lower = text.toLowerCase();
        const qLow  = q.toLowerCase();
        const qLen  = q.length;
        let i = 0;
        const frags = [];
        while (i < text.length) {
          const idx = lower.indexOf(qLow, i);
          if (idx === -1) { frags.push(document.createTextNode(text.slice(i))); break; }
          if (idx > i) frags.push(document.createTextNode(text.slice(i, idx)));
          const m = document.createElement('mark');
          m.className = 'arrival-hl';
          m.textContent = text.slice(idx, idx + qLen);
          frags.push(m);
          marks.push(m);
          i = idx + qLen;
        }
        if (frags.length > 1) {
          const parent = node.parentNode;
          frags.forEach(function(f) { parent.insertBefore(f, node); });
          parent.removeChild(node);
        }
      } else if (node.nodeType === 1
          && node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE'
          && node.tagName !== 'MARK'  && node.tagName !== 'A') {
        Array.from(node.childNodes).forEach(walkNode);
      }
    }

    walkNode(article);

    // Scroll the first match into view (if we didn't land on a hash)
    if (marks.length > 0 && !location.hash) {
      marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Fade out after 4 s, then remove the <mark> nodes cleanly
    setTimeout(function() {
      marks.forEach(function(m) {
        m.style.transition = 'background 1.5s, color 1.5s';
        m.style.background = 'transparent';
        m.style.color = 'inherit';
      });
      setTimeout(function() {
        marks.forEach(function(m) {
          if (m.parentNode) {
            m.parentNode.insertBefore(document.createTextNode(m.textContent), m);
            m.parentNode.removeChild(m);
          }
        });
      }, 1600);
    }, 4000);
  })();

  // ── Inline section nav (only for the active chapter) ──────────────────────
  (function buildInlineSectionNav() {
    const article    = document.querySelector('article');
    const activeLink = document.querySelector('.chapter-list a.active');
    if (!article || !activeLink) return;

    const headings = Array.from(article.querySelectorAll('h2, h3'));
    if (headings.length < 2) return;

    // Ensure IDs exist (python-markdown toc extension adds these, but just in case)
    headings.forEach((h, i) => { if (!h.id) h.id = 'sec-' + i; });

    // Build inline <ul> to insert after the active chapter link
    const ul = document.createElement('ul');
    ul.className = 'inline-section-nav';
    ul.innerHTML = headings.map(h => {
      const cls = h.tagName === 'H3' ? ' class="sec-h3"' : '';
      return '<li' + cls + '><a class="sec-link" href="#' + h.id + '">' + h.textContent + '</a></li>';
    }).join('');

    // Insert right inside the active <li>, below the chapter link
    activeLink.parentElement.appendChild(ul);

    // Scrollspy — highlight current section in the inline nav
    const links = ul.querySelectorAll('a.sec-link');
    let activeId = null;

    function setActive(id) {
      if (id === activeId) return;
      activeId = id;
      links.forEach(a => a.classList.toggle('sec-active', a.getAttribute('href') === '#' + id));
      // Scroll the active link into view within the sidebar
      const activeA = ul.querySelector('a.sec-active');
      if (activeA) activeA.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => { if (e.isIntersecting) setActive(e.target.id); });
    }, { rootMargin: '0px 0px -65% 0px', threshold: 0 });

    headings.forEach(h => observer.observe(h));

    // Scroll fallback
    let t;
    window.addEventListener('scroll', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        let cur = headings[0];
        headings.forEach(h => { if (h.getBoundingClientRect().top <= 80) cur = h; });
        if (cur) setActive(cur.id);
      }, 50);
    }, { passive: true });
  })();

})();
</script>
</body>
</html>
"""


def _fill_template(template, **kwargs):
    """Replace __PLACEHOLDER__ tokens in template string."""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f'__{key.upper()}__', str(value))
    return result


def make_breadcrumb(meta):
    """Generate breadcrumb from frontmatter."""
    parts = []
    if meta.get('part'):
        parts.append('<a href="00-Table-of-Contents.html">Contents</a>')
        parts.append(f'<span>{meta["part"]}</span>')
    if not parts:
        return ''
    sep   = '<span class="sep">›</span>'
    inner = sep.join(parts)
    return f'<nav class="breadcrumb" aria-label="breadcrumb">{inner}</nav>'


def make_page_title(meta, stem):
    if meta.get('title'):
        return meta['title']
    return stem.replace('-', ' ').replace('_', ' ')


def wrap_page(content_html, meta, stem):
    sidebar    = build_sidebar(active_stem=stem)
    page_title = make_page_title(meta, stem)
    breadcrumb = make_breadcrumb(meta)

    # Prefer the frontmatter date (revised > created) over the build timestamp.
    # YAML parses bare dates as datetime.date objects, so convert to string.
    fm_date = meta.get('revised') or meta.get('created')
    if fm_date:
        build_time = str(fm_date)          # e.g. "2026-03-11"
    else:
        build_time = datetime.now().strftime('%Y-%m-%d')

    return _fill_template(
        HTML_TEMPLATE,
        page_title   = page_title,
        site_title   = SITE_TITLE,
        site_subtitle = SITE_SUBTITLE,
        sidebar      = sidebar,
        breadcrumb   = breadcrumb,
        content      = content_html,
        build_time   = build_time,
    )


# ── File discovery ────────────────────────────────────────────────────────────
def discover_md_files():
    files = []
    for root, dirs, filenames in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
        for fname in filenames:
            if fname.endswith('.md') and fname not in EXCLUDE_FILES:
                files.append(Path(root) / fname)
    return sorted(files)


def file_hash(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


# ── Build ─────────────────────────────────────────────────────────────────────
def build_file(md_path, stem=None):
    if stem is None:
        stem = md_path.stem

    text = md_path.read_text(encoding='utf-8')
    meta, content_html = convert_file(text)
    page_html = wrap_page(content_html, meta, stem)

    out_path = SITE_DIR / f'{stem}.html'
    out_path.write_text(page_html, encoding='utf-8')
    return out_path


def run_build(incremental=False):
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    # GitHub Pages: disable Jekyll so _-prefixed files are served correctly
    nojekyll = SITE_DIR / '.nojekyll'
    if not nojekyll.exists():
        nojekyll.touch()
        print('  ✔ .nojekyll created')

    # Copy stylesheet
    if STYLE_SRC.exists():
        shutil.copy(STYLE_SRC, SITE_DIR / 'style.css')
        print('  ✔ style.css copied')

    md_files  = discover_md_files()
    cache     = load_cache() if incremental else {}
    new_cache = {}
    built     = 0
    skipped   = 0

    for md_path in md_files:
        stem  = md_path.stem
        fhash = file_hash(md_path)
        new_cache[str(md_path)] = fhash

        if incremental and cache.get(str(md_path)) == fhash:
            skipped += 1
            continue

        try:
            build_file(md_path, stem)
            print(f'  ✔ {stem}.html')
            built += 1
        except Exception as e:
            print(f'  ✘ {stem}: {e}')

    # Always regenerate search index (cheap operation)
    build_search_index(md_files)
    print('  ✔ search-index.js')

    # index.html → copy of TOC
    toc_html = SITE_DIR / '00-Table-of-Contents.html'
    index    = SITE_DIR / 'index.html'
    if toc_html.exists():
        shutil.copy(toc_html, index)
        print('  ✔ index.html → TOC')

    save_cache(new_cache)
    print(f'\nBuild complete: {built} built, {skipped} unchanged')
    print(f'Output: {VAULT_ROOT} (repo root)')


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    incremental = '--incremental' in sys.argv
    mode = 'incremental' if incremental else 'full'
    print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting {mode} build...')
    run_build(incremental=incremental)
