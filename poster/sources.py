"""Where stories come from. Only sources meant for this use:
- publishers' own RSS feeds (headlines + links) -> a story carried by several outlets at once is trending
- the article pages themselves -> fact-check text and the X posts the newsroom embedded
- GDELT DOC API (open data, free for commercial use with credit) -> more coverage of a story
- Wikipedia/Commons -> freely licensed photos, credited
"""
import email.utils
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .dedupe import words
from .net import get, get_json, q

FEEDS = {
    'NPR': 'https://feeds.npr.org/1001/rss.xml',
    'CBS News': 'https://www.cbsnews.com/latest/rss/main',
    'NBC News': 'https://feeds.nbcnews.com/nbcnews/public/news',
    'ABC News': 'https://feeds.abcnews.com/abcnews/topstories',
    'Fox News': 'https://moxie.foxnews.com/google-publisher/latest.xml',
    'Yahoo News': 'https://news.yahoo.com/rss/',
    'NY Post': 'https://nypost.com/feed/',
    'The Hill': 'https://thehill.com/feed/',
    'Politico': 'https://rss.politico.com/politics-news.xml',
    'CNBC': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114',
    'CBS Sports': 'https://www.cbssports.com/rss/headlines/',
    'Variety': 'https://variety.com/feed/',
    'Deadline': 'https://deadline.com/feed/',
    'TMZ': 'https://www.tmz.com/rss.xml',
    'E! News': 'https://feeds.feedburner.com/eonline/topstories',
    'Us Weekly': 'https://www.usmagazine.com/feed/',
    'The Verge': 'https://www.theverge.com/rss/index.xml',
}
ATOM = '{http://www.w3.org/2005/Atom}'
MAX_AGE_H = 12
TWEET = re.compile(r'(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{10,20})')
_last_gdelt = [0.0]


def _feed(name_url):
    name, url = name_url
    try:
        root = ET.fromstring(get(url, timeout=15, raw=True))
    except Exception:
        return []
    out = []
    for it in list(root.iter('item')) + list(root.iter(ATOM + 'entry')):
        title = (it.findtext('title') or it.findtext(ATOM + 'title') or '').strip()
        link = it.findtext('link') or ''
        if not link and it.find(ATOM + 'link') is not None:
            link = it.find(ATOM + 'link').get('href', '')
        when = it.findtext('pubDate') or it.findtext(ATOM + 'updated') or it.findtext(ATOM + 'published')
        try:
            ts = email.utils.parsedate_to_datetime(when) if when and ',' in when else datetime.fromisoformat(when.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        except Exception:
            age = None
        if title and link and (age is None or age <= MAX_AGE_H):
            out.append({'outlet': name, 'title': html.unescape(title), 'url': link.strip(), 'age_h': age})
    return out


def trending():
    """Cluster fresh headlines from all feeds; a cluster covered by more outlets ranks higher."""
    with ThreadPoolExecutor(8) as ex:
        items = [i for rows in ex.map(_feed, FEEDS.items()) for i in rows]
    clusters = []
    for it in items:
        w = words(it['title'])
        if len(w) < 3:
            continue
        for c in clusters:
            shared = len(w & c['words'])
            if shared >= 3 or shared / max(1, len(w | c['words'])) >= 0.3:
                c['items'].append(it)
                c['words'] |= w
                break
        else:
            clusters.append({'words': set(w), 'items': [it]})
    out = []
    for c in clusters:
        outlets = sorted({i['outlet'] for i in c['items']})
        ages = [i['age_h'] for i in c['items'] if i['age_h'] is not None]
        out.append({'title': c['items'][0]['title'], 'outlets': outlets, 'links': c['items'],
                    'fresh_h': round(min(ages), 1) if ages else None})
    out.sort(key=lambda c: (-len(c['outlets']), c['fresh_h'] if c['fresh_h'] is not None else 99))
    return out[:40]


def _gdelt(query, n=10):
    """GDELT asks for at most one request every 5 s; wait instead of hammering."""
    wait = 5.5 - (time.time() - _last_gdelt[0])
    if wait > 0:
        time.sleep(wait)
    _last_gdelt[0] = time.time()
    try:
        d = json.loads(get('https://api.gdeltproject.org/api/v2/doc/doc?query={}%20sourcelang:english'
                           '&mode=ArtList&maxrecords={}&timespan=1d&sort=HybridRel&format=json'.format(q(query), n), timeout=20))
    except Exception:
        return []
    return [{'outlet': a['domain'], 'url': a['url'], 'title': a['title']} for a in d.get('articles', [])]


def _page(url):
    try:
        h = get(url, timeout=15)
    except Exception:
        return None
    paras = [html.unescape(re.sub(r'<[^>]+>', '', p)).strip() for p in re.findall(r'<p[^>]*>(.*?)</p>', h, re.S)]
    tweets = [i for u, i in TWEET.findall(h) if u.lower() not in ('intent', 'i', 'share', 'home', 'search')]
    return {'text': ' '.join(p for p in paras if len(p) > 60)[:3000], 'tweets': tweets}


def articles(cluster, query, want=3):
    """Fact-check sources (distinct outlets with readable text) + X posts embedded by those newsrooms,
    ranked by how many outlets embedded the same post."""
    links = [dict(i) for i in cluster['links']]
    if len({i['outlet'] for i in links}) < 3:
        links += _gdelt(query)
    seen_url, cands = set(), []
    for l in links:
        if l['url'] not in seen_url:
            seen_url.add(l['url'])
            cands.append(l)
    cands = cands[:12]
    with ThreadPoolExecutor(6) as ex:
        pages = list(ex.map(lambda c: _page(c['url']), cands))
    outlets, embeds, seen = [], {}, set()
    for c, p in zip(cands, pages):
        if not p:
            continue
        for tid in set(p['tweets']):
            embeds[tid] = embeds.get(tid, 0) + 1
        if c['outlet'] not in seen and len(p['text']) >= 400 and len(outlets) < want:
            seen.add(c['outlet'])
            outlets.append({'outlet': c['outlet'], 'url': c['url'], 'title': c['title'], 'text': p['text']})
    ranked = [t for t, _ in sorted(embeds.items(), key=lambda kv: -kv[1])]
    return outlets, ranked


def wiki_photo(title):
    """Lead photo of a Wikipedia article, only if Commons licenses it freely. Returns (url, credit) or None."""
    try:
        d = get_json('https://en.wikipedia.org/w/api.php?action=query&format=json&redirects=1'
                     '&prop=pageimages&piprop=original|name&titles=' + q(title))
        page = next(iter(d['query']['pages'].values()))
        src, fname = page['original']['source'], page['pageimage']
        m = get_json('https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo'
                     '&iiprop=extmetadata|size&titles=File:' + q(fname))
        info = next(iter(m['query']['pages'].values()))['imageinfo'][0]
    except Exception:
        return None
    meta = info['extmetadata']
    lic = meta.get('LicenseShortName', {}).get('value', '')
    if not re.match(r'(CC BY|CC0|Public domain|PD)', lic, re.I) or info.get('width', 0) < 600:
        return None
    if not src.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):  # SVG logos/seals render as a broken box
        return None
    artist = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', meta.get('Artist', {}).get('value', 'Unknown'))).strip()
    return src, 'Photo: {} / Wikimedia Commons ({})'.format(artist, lic)
