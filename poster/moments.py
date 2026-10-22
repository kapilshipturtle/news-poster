"""Real moments for Reels, the way Channel Studio's news brain finds them: YouTube storyboards looked at by a vision
model so only real footage of the event/person/place is cut (3-5 s sections), plus real photos from Bing images."""
import email
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from . import ai

YTDLP = os.environ.get('YTDLP', os.path.join(os.path.dirname(sys.executable), 'yt-dlp'))  # the venv's yt-dlp
REACTION = re.compile(r'(?i)\b(react(s|ion|ing)?|analy(sis|st|zes?)|commentary|podcast|explained|breakdown|opinion|debate|'
                      r'discuss(es|ion)?|panel|rant|live ?stream|livestream|q ?& ?a|my thoughts|full (episode|show))\b')
PAUSE = 2.0  # seconds between YouTube requests (rate limits / bot checks)
_last = [0.0]
BING_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9', 'sec-ch-ua': '"Chromium";v="129", "Not=A?Brand";v="8"', 'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"', 'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-origin'}

CHECK = """You are the picture editor of a US news page checking pictures for a vertical news Reel about this story:
"{story}"
People/places/organisations in it: {names}
Each image below is a contact sheet of numbered pictures. KEEP a number only when it is a real photo or real footage that
truly shows this story's person, place, organisation or event. A news channel's own logo or a chyron ABOUT THIS STORY is fine.
REJECT: a studio presenter, anchor, reporter or commentator talking to camera; a podcast/reaction frame; a stock-agency
watermark (getty, alamy, shutterstock, istock...); a chyron or headline about ANOTHER story; burned-in subtitles; split screens,
collages, thumbnails, quote cards, text-heavy cards, title cards, memes; the wrong person or place; a child as the focus;
AI-looking, blurry or tiny pictures; black or blank frames.
Return JSON: {{"<sheet letter>": {{"<number>": {{"keep": true|false, "desc": "what it shows, <= 12 words"}}}}}} for every number."""


def _yt(args, timeout=120):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    return subprocess.run([YTDLP, '--no-warnings'] + args, capture_output=True, text=True, timeout=timeout)


def yt_search(query, subject_words, n=10):
    """Recent videos for the story, reaction/analysis titles dropped, the subject's own channel first."""
    r = _yt(['--flat-playlist', '--print', '%(id)s\t%(duration)s\t%(channel)s\t%(title)s', 'ytsearch{}:{}'.format(n, query)])
    out = []
    for line in r.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        vid, dur, chan, title = parts[0], parts[1], parts[2], parts[3]
        try:
            dur = float(dur)
        except ValueError:
            continue
        if not 20 <= dur <= 2400 or REACTION.search(title) or REACTION.search(chan):
            continue
        own = any(w in chan.lower() for w in subject_words if len(w) > 3)
        out.append({'id': vid, 'dur': dur, 'channel': chan, 'title': title, 'own': own})
    return sorted(out, key=lambda v: not v['own'])


def storyboard(vid, work):
    """[(t, jpg)] frames from YouTube's storyboard sheets (the video itself is not downloaded)."""
    d = os.path.join(work, 'sb', vid)
    os.makedirs(d, exist_ok=True)
    r = _yt(['-J', 'https://youtu.be/' + vid], timeout=120)
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return []
    w, h = j.get('width') or 0, j.get('height') or 0
    if w and h and w / h < 1.5:  # B-roll must be wide (16:9); vertical and square videos are skipped
        return []
    sb = [f for f in j.get('formats') or [] if str(f.get('format_id', '')).startswith('sb')]
    if not sb:
        return []
    fmt = max(sb, key=lambda f: (f.get('width') or 0))
    _yt(['-f', fmt['format_id'], '-o', os.path.join(d, 'sb.%(ext)s'), 'https://youtu.be/' + vid], timeout=120)
    mh = next((os.path.join(d, f) for f in os.listdir(d) if f.startswith('sb.')), None)
    if not mh:
        return []
    from PIL import Image
    msg = email.message_from_bytes(open(mh, 'rb').read())
    tiles = [p.get_payload(decode=True) for p in msg.walk() if p.get_content_type().startswith('image/')]
    rows, cols = int(fmt.get('rows') or 3), int(fmt.get('columns') or 3)
    duration = j.get('duration') or 0
    step = duration / max(1, len(tiles) * rows * cols)
    frames, k = [], 0
    for raw in tiles:
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        tw, th = im.width // cols, im.height // rows
        for r_ in range(rows):
            for c_ in range(cols):
                t, k = k * step, k + 1
                if t > duration - 6:
                    continue
                cell = im.crop((c_ * tw, r_ * th, (c_ + 1) * tw, (r_ + 1) * th))
                if max(e[1] for e in cell.getextrema()) < 25:
                    continue
                p = os.path.join(d, 'f{:04d}.jpg'.format(k))
                cell.save(p, quality=88)
                frames.append((round(t, 1), p))
    return frames


def bing_images(query, n=24):
    """Real photos from Bing images (no browser): [{url, thumb, w, h}] of at least 900 px wide."""
    qs = urllib.parse.urlencode({'q': query, 'first': 1, 'count': 35, 'qft': '+filterui:aspect-wide', 'mmasync': 1})
    req = urllib.request.Request('https://www.bing.com/images/async?' + qs, headers=dict(
        BING_HEADERS, Referer='https://www.bing.com/images/search?q=' + urllib.parse.quote_plus(query)))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            doc = r.read().decode('utf-8', 'ignore')
    except Exception:
        return []
    rows = []
    for chunk in doc.split('class="iusc"')[1:]:
        m = re.search(r'\bm="([^"]+)"', chunk)
        wh = re.search(r'<span class="nowrap">\s*(\d+)\s*(?:&#215;|×|x)\s*(\d+)', chunk)
        if not m:
            continue
        try:
            d = json.loads(html.unescape(m.group(1)))
        except ValueError:
            continue
        w, h = (int(wh.group(1)), int(wh.group(2))) if wh else (0, 0)
        if d.get('murl') and d.get('turl') and w >= 900 and h and w / h >= 1.45:  # landscape, close to 16:9
            rows.append({'url': d['murl'], 'thumb': d['turl'], 'w': w, 'h': h})
    return rows[:n]


def _sheet(items, path, label):
    """Contact sheet: up to 20 pictures in a 5-wide grid, each numbered."""
    from PIL import Image, ImageDraw
    cw, ch = 320, 180
    rows = (len(items) + 4) // 5
    sheet = Image.new('RGB', (cw * 5, ch * rows), 'white')
    dr = ImageDraw.Draw(sheet)
    for i, im in enumerate(items):
        x, y = (i % 5) * cw, (i // 5) * ch
        sheet.paste(im.resize((cw, ch)), (x, y))
        dr.rectangle([x, y, x + 34, y + 24], fill='black')
        dr.text((x + 6, y + 5), str(i + 1), fill='yellow')
    sheet.save(path, quality=85)
    return label


def find(story, names, query, work, want=18):
    """Checked real moments from up to 5 different wide videos (at most 2 from one video, 20+ s apart) and Bing photos,
    returned clip/photo alternating so no single source dominates the Reel."""
    from PIL import Image
    os.makedirs(work, exist_ok=True)
    words = [w.lower() for n in names for w in re.findall(r'[A-Za-z]+', n)]
    videos, seen = [], set()
    for q in [query] + ['{} {}'.format(n, query.split()[0]) for n in names[:2]]:
        for v in yt_search(q, words):
            if v['id'] not in seen:
                seen.add(v['id'])
                videos.append(v)
    videos.sort(key=lambda v: not v['own'])
    sheets, index = [], {}
    letter = iter('ABCDEFGHIJKL')
    for v in videos:
        if len(index) >= 5:
            break
        frames = storyboard(v['id'], work)
        if not frames:
            continue
        pick = frames[::max(1, len(frames) // 20)][:20]
        L = next(letter)
        path = os.path.join(work, 'sheet_{}.jpg'.format(L))
        _sheet([Image.open(p) for _, p in pick], path, L)
        sheets.append(path)
        index[L] = [{'kind': 'clip', 'vid': v['id'], 't': t, 'channel': v['channel']} for t, _ in pick]
    photos = bing_images(query)
    if photos:
        thumbs, kept = [], []
        for ph in photos[:20]:
            try:
                raw = urllib.request.urlopen(urllib.request.Request(ph['thumb'], headers={'User-Agent': BING_HEADERS['User-Agent']}),
                                             timeout=15).read()
                thumbs.append(Image.open(io.BytesIO(raw)).convert('RGB'))
                kept.append(ph)
            except Exception:
                continue
        if thumbs:
            L = next(letter)
            path = os.path.join(work, 'sheet_{}.jpg'.format(L))
            _sheet(thumbs, path, L)
            sheets.append(path)
            index[L] = [{'kind': 'photo', 'url': ph['url'], 'thumb': ph['thumb']} for ph in kept]
    if not sheets:
        return []
    verdict = ai.gemini_vision(CHECK.format(story=story, names=', '.join(names)) +
                               '\nSheets in order: ' + ', '.join(index), sheets)
    clips, photos_ok = [], []
    for L, items in index.items():
        marks = verdict.get(L) or {}
        taken = []
        for i, item in enumerate(items):
            m = marks.get(str(i + 1)) or {}
            if not m.get('keep'):
                continue
            if item['kind'] == 'clip':
                if len(taken) >= 2 or any(abs(item['t'] - t) < 20 for t in taken):  # variety: 2 per video, 20 s apart
                    continue
                taken.append(item['t'])
                clips.append(dict(item, desc=m.get('desc', '')))
            else:
                photos_ok.append(dict(item, desc=m.get('desc', '')))
    out = []
    while clips or photos_ok:  # alternate, starting with a clip
        if clips:
            out.append(clips.pop(0))
        if photos_ok:
            out.append(photos_ok.pop(0))
    return out[:want]


def cut(vid, t, dur, out):
    """Download only the seconds needed (720p is plenty for a 1080-wide band and saves the free VM's CPU)."""
    r = _yt(['--download-sections', '*{:.1f}-{:.1f}'.format(t, t + dur), '--force-keyframes-at-cuts', '-f',
             'bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/bv*+ba', '--merge-output-format', 'mp4', '-o', out,
             'https://youtu.be/' + vid], timeout=300)
    return out if os.path.exists(out) and os.path.getsize(out) > 20000 else None


def photo(url, out):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': BING_HEADERS['User-Agent']}), timeout=25).read()
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        if im.width < 700:
            return None
        im.save(out, quality=92)
        return out
    except Exception:
        return None
