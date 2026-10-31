"""Indian Daily News long videos (user 2026-10-31: "yes go ahead and keep this ... video length 3 minute +"): 5 a day (10-31),
replacing the Page's short Reels, in The Shiva Kumar's measured format (cloudrun/viral_render.py draws it):
  - stories about PEOPLE going viral (influencer drama, underdog rise, unusual love, oddities) - his 0.6-2.8M-view hits;
    plain government news got ~1% of that (measured on his last 9 + Black Bharat TV's last 9);
  - his opening: 4-5 teaser lines with a withheld detail / contradiction, each on its own hook card with a whoosh-boom,
    ending in a question + "सब देखिए"; story from "दरअसल"; the question re-opened mid-way; the answer only at the end;
    comment ask + disclaimer;
  - the cloned voice (voice-studio "shiva-hindi" reference, sent with every Fish request), one burst of a clip's own sound.
Runs under PROFILE=hi (Page 3 token, Hindi feeds, OCR + picture checks from hindi.py); renders on Cloud Run."""
import json
import os
import random
import re
import shutil
import struct
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import ai, cloud, hindi, moments, schedule

IST = ZoneInfo('Asia/Kolkata')
QUEUE = os.path.join(schedule.STATE, 'viral_queue')
PER_DAY = 5                # user 2026-10-31: 5 a day, every one of them published
WINDOWS = [(7 * 60, 8 * 60 + 30), (10 * 60, 11 * 60 + 30), (13 * 60, 14 * 60 + 30), (16 * 60 + 30, 18 * 60),
           (20 * 60, 21 * 60 + 30)]  # IST, one video in each
POST_HOURS = (6, 23)       # user rule 10-24: post only 06:00-23:00 local; a missed slot is caught up before 23:00
BUFFER = 2                 # videos kept built ahead, at any hour, so no slot waits on a ~20-min build
VOICE_DIR = os.environ.get('VIRAL_VOICE_DIR', '/home/poster/voices/shiva-hindi')
SFX = os.environ.get('VIRAL_SFX', '/home/poster/sfx/viral_hit.wav')  # Meta SC whoosh_2 + impact_1 -6 dB (measured match)
WORDS = (480, 650)        # the clone reads ~158 words/min (measured 10-31) -> about 3:00-4:10 like his 2:40-4:17
VIRAL_FEEDS = {  # MEASURED 2026-10-31: these answer with fresh people/oddity stories and direct article links
    'TV9 Hindi': 'https://www.tv9hindi.com/trending/feed',          # 30 items, dated
    'India TV': 'https://www.indiatv.in/rssnews/topstory-viral-news.xml',  # 31 items, dated
    'ABP Live': 'https://www.abplive.com/trending/feed',               # 10 items, dated
    'News18 Hindi': 'https://hindi.news18.com/rss/khabar/ajab-gajab.xml',  # 200 items, NO dates: counts only with a 2nd outlet
}  # tested and NOT working: Aaj Tak, Amar Ujala, Jagran, Patrika (404), NDTV (403), Live Hindustan (503), Zee (no answer)
MAX_USE = 3               # one picture on screen at most 3 times (he repeats pictures too) - more only when few exist
ENOUGH = 16               # different pictures worth fetching for a ~3.5-min video (each shows ~2-3 times)
MIN_PICS = 6              # different pictures a video needs (story video pieces count: his videos replay the viral clip)
MAX_SHOT = 7.5            # his median shot is 6.6 s; a longer sentence gets two pictures
moments.PREFER_VERTICAL = False  # the media window is 16:9, not a phone screen

PICK = """You run an Indian Hindi Facebook page whose long videos go viral: stories about specific PEOPLE that everyone is
talking about on social media - an influencer feud or emotional breakdown, a poor person's rise ("झुग्गी से सुपरमॉडल"),
an unusual love story, a kind or brave act, a stunning "अजब-गजब" oddity, a family drama that went viral.
NEVER: government orders, policy, elections or party politics, markets, routine crime or accidents without a human story.
From the candidates, choose the 4 with the strongest human drama that Indians are discussing right now.
Do NOT pick any story in RECENT (even reworded).
CANDIDATES:
{cands}
RECENT:
{recent}
Return JSON: {{"picks": [{{"n": <candidate number>, "query": "<4-8 word ENGLISH search query for this story>",
"query_hi": "<4-8 word Hindi search query>", "names": ["<the people in it, in English, max 3>"]}}]}}"""

SCRIPT = """Write a Hindi Facebook news video script in the exact style of India's most viral people-story pages.
Use ONLY facts in the SOURCES. Never invent quotes, numbers, names or events. If two sources disagree on a detail (an age,
a number), leave that detail out - do NOT skip the story for it. Return {{"skip": "<reason>"}} only when the sources do not
say what actually happened.

STRUCTURE (this is what makes people watch to the end):
1. "teaser": 4 or 5 punchy spoken lines (8-16 words each) BEFORE the story. Each line is a new claim. Use the patterns:
   withheld detail ("... ने लगाया ऐसा आरोप कि ..." - never say WHAT yet), contradiction ("कभी ... आज ...",
   "न ... न ...", "जिसे ... उसी ने ..."), emotion ("फूट-फूट कर रोने लगी"). The LAST teaser line asks the big question and
   promises the answer: e.g. "आखिर ... ? सब देखिए।" (Each teaser line is also shown big on screen, word for word.)
2. "story": 30-45 spoken sentences (each max 20 words). Starts with "दरअसल,". Tell it in time order with real details
   and the emotions. Around the middle, re-open the question ("अब ये जानना बहुत ज़रूरी है कि ..."). Keep the answer to the
   teaser question for the LAST 20% of the story. End with: "आपको क्या लगता है? कमेंट कर प्रतिक्रिया ज़रूर दीजिएगा।" and
   "दी गई जानकारी सोशल मीडिया और मीडिया रिपोर्ट्स पर आधारित है।"
Total spoken words (teaser + story): {lo} to {hi}. Simple spoken Hindi like a TV anchor; numbers as Hindi words.
"headline": ONE sentence split into 4 SHORT lines of 2-3 words each, no comma at a line end (it stays on screen the whole video): lines 1-2 are the
setup, lines 3-4 the payoff, line 3 ends with ".." (e.g. "जिसे घर में रखा" / "उसी मेहमान ने" / "लगाया आरोप.." / "रो पड़ीं शिवानी").
"title": the Facebook post title in HINDI, max 110 characters, hook + contradiction like the teaser, no hashtags
(e.g. "इज्जत के चक्कर में मार्विन आची ने शिवानी कुमारी पर लगाया आरोप, फूट फूट कर रोने लगी शिवानी!").
"hashtags": 5-8 ENGLISH hashtags, lowercase, no spaces: each person's name (e.g. "#shivanikumari", "#marvinachi"),
the story's key words, and always "#viralvideo" and "#trending".

Return JSON: {{"teaser": [{{"say": "..."}}], "story": ["...", "..."], "headline": ["..", "..", "..", ".."],
"title": "...", "hashtags": ["#..", "#.."], "names": ["<people, English>"]}}

SOURCES:
{sources}"""

MATCH = """Pick a picture for each spoken sentence of a Hindi news video about: {story}
SENTENCES:
{sents}
PICTURES (number: what it shows):
{pics}
Use each picture at most 3 times; prefer the picture that shows who or what the sentence talks about; spread them out.
Return JSON: {{"pick": [<picture number for sentence 0>, <for sentence 1>, ...]}} (exactly {n} numbers)."""

CHECK = """These numbered pictures go into a Hindi news video about: {story}. People in the story: {names}.
Mark a picture BAD if: it is a TV graphic, headline banner, breaking-news frame, subtitles, a quote card or a screenshot
with long text; a split screen or collage; a watermark across it; blank, black or blurry; or it clearly shows people or
places that have nothing to do with this story. A small channel logo in a corner is fine.
Return JSON: {{"bad": [<numbers>]}}"""


# ---- small helpers ---------------------------------------------------------------------------------------------------
def log(*a):
    print(*a, flush=True)


def now_ist():
    return schedule.now().astimezone(IST)


def _load(name, default):
    return schedule._load(name, default)


def posted_today():
    d = now_ist().date()
    return [p for p in _load('viral_posted.json', []) if datetime.fromisoformat(p['time']).astimezone(IST).date() == d]


def ready():
    os.makedirs(QUEUE, exist_ok=True)
    out = []
    for d in sorted(os.listdir(QUEUE)):
        m = os.path.join(QUEUE, d, 'meta.json')
        if os.path.exists(m):
            meta = json.load(open(m))
            if schedule.now() - datetime.fromisoformat(meta['built']) > timedelta(hours=20) and not meta.get('attempted'):
                shutil.rmtree(os.path.join(QUEUE, d), ignore_errors=True)  # stale (people stories last a day; night builds post at 07:00)
                continue
            out.append(dict(meta, dir=os.path.join(QUEUE, d)))
    return out


def _dur(path):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                                capture_output=True, text=True).stdout or 0)


# ---- the cloned voice (Fish, reference sent with every request: the free plan cannot store more voices) --------------
def _mp(obj):
    """Minimal MessagePack encoder (Fish takes binary reference audio only as MessagePack)."""
    if obj is None:
        return b'\xc0'
    if obj is True or obj is False:
        return b'\xc3' if obj else b'\xc2'
    if isinstance(obj, int):
        return b'\xd3' + struct.pack('>q', obj)
    if isinstance(obj, float):
        return b'\xcb' + struct.pack('>d', obj)
    if isinstance(obj, str):
        b = obj.encode()
        return (bytes([0xa0 | len(b)]) if len(b) < 32 else b'\xdb' + struct.pack('>I', len(b))) + b
    if isinstance(obj, bytes):
        return b'\xc6' + struct.pack('>I', len(obj)) + obj
    if isinstance(obj, (list, tuple)):
        return b'\xdd' + struct.pack('>I', len(obj)) + b''.join(_mp(x) for x in obj)
    if isinstance(obj, dict):
        return b'\xdf' + struct.pack('>I', len(obj)) + b''.join(_mp(k) + _mp(v) for k, v in obj.items())
    raise TypeError(type(obj))


def _speak(text, out, ref, ref_text, key):
    body = _mp({'text': text, 'references': [{'audio': ref, 'text': ref_text}], 'format': 'wav', 'normalize': True,
                'latency': 'normal', 'chunk_length': 300, 'temperature': 0.7, 'top_p': 0.7,
                'prosody': {'speed': 1.0, 'volume': 0.0, 'normalize_loudness': True}})  # user rule: speed always 1.0
    req = urllib.request.Request('https://api.fish.audio/v1/tts', data=body, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/msgpack', 'model': 's2.1-pro-free'})
    for attempt in range(3):
        try:
            open(out, 'wb').write(urllib.request.urlopen(req, timeout=300).read())
            if _dur(out) > 0.3:
                return out
        except (urllib.error.URLError, OSError) as e:
            log('  voice retry %d: %s' % (attempt + 1, str(e)[:100]))
        time.sleep(5 * (attempt + 1))
    raise ai.AIUnavailable('Fish voice failed 3 times')


def voice(sentences, work):
    """One request per sentence (parallel x4): exact sentence timings without alignment. His delivery is near
    continuous (only ~4 pauses a minute), so the joins are short."""
    ref = open(os.path.join(VOICE_DIR, 'ref.wav'), 'rb').read()
    ref_text = open(os.path.join(VOICE_DIR, 'ref.txt')).read().strip()
    key = open(os.path.expanduser(os.environ.get('FISH_KEY_FILE', '~/.fish_api_key'))).read().strip()
    d = os.path.join(work, 'v')
    os.makedirs(d, exist_ok=True)
    outs = [os.path.join(d, '%03d.wav' % i) for i in range(len(sentences))]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda a: _speak(a[0], a[1], ref, ref_text, key), zip(sentences, outs)))
    gap = os.path.join(d, 'gap.wav')
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.14', gap], check=True)
    lst, cuts, t = [], [], 0.0
    for o in outs:
        cuts.append(t)
        lst += [o, gap]
        t += _dur(o) + 0.14
    cuts.append(t)
    wav = os.path.join(work, 'voice.wav')
    inp = sum((['-i', f] for f in lst), [])
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', *inp, '-filter_complex',
                    ''.join('[%d:a]aformat=sample_rates=44100:channel_layouts=mono[a%d];' % (k, k) for k in range(len(lst))) +
                    ''.join('[a%d]' % k for k in range(len(lst))) + 'concat=n=%d:v=0:a=1[o]' % len(lst),
                    '-map', '[o]', wav], check=True)
    return wav, cuts


# ---- story, script, pictures -------------------------------------------------------------------------------------------
STOP = set('के की का में है हैं और से को ने पर यह वह एक लिए कर तो भी ही हो गया गई गए रहा रही रहे था थी थे बाद साथ अब '
           'वीडियो वायरल देख देखकर देखें लोग यूजर्स बोले हुआ हुई हुए करने करते किया दिया लिया अपने अपनी उसके उनके इस उस '
           'video viral the and for with'.split())


def _items(name, url):
    """A tolerant reader for the viral feeds (live 10-31: News18's dates sit in CDATA and the general reader dropped
    all 200 of its items)."""
    import email.utils
    import html
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read()
    except (urllib.error.URLError, OSError):
        return []
    out = []
    for it in re.findall(r'<item>(.*?)</item>', raw.decode('utf-8', 'ignore'), re.S)[:40]:
        def tag(t):
            m = re.search(r'<%s>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</%s>' % (t, t), it, re.S)
            return html.unescape(m.group(1).strip()) if m else ''
        title, link, age = tag('title'), tag('link'), None
        try:
            age = (schedule.now() - email.utils.parsedate_to_datetime(tag('pubDate'))).total_seconds() / 3600
        except (TypeError, ValueError):
            pass
        if title and link.startswith('http') and (age is None or age <= 24):
            out.append({'outlet': name, 'title': title, 'url': link, 'age_h': age})
    return out


def _words(t):
    """Hindi words of 3+ letters, plus numbers and short Latin tokens ("40", "km", "ias"): those mark the same story
    across outlets (TV9 and India TV's water-bike story shared only 2 Hindi words, but also "40" and "km")."""
    t = t.lower()
    return ({w for w in re.findall(r'[\u0900-\u097F]+', t) if len(w) >= 3 and w not in STOP} |
            {w for w in re.findall(r'[a-z0-9]+', t) if len(w) >= 2 and w not in STOP})


def candidates():
    """Hindi-aware grouping (the general one keeps only English words: live 10-31 it turned 23 Hindi headlines into 0
    stories). A story counts when 2+ outlets carry it, or when one outlet dated it within 24 h."""
    with ThreadPoolExecutor(4) as ex:
        items = [i for rows in ex.map(lambda kv: _items(*kv), VIRAL_FEEDS.items()) for i in rows]
    clusters = []
    for it in items:
        w = _words(it['title'])
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
        fresh = min(ages) if ages else None
        if len(outlets) >= 2 or fresh is not None:
            out.append({'title': c['items'][0]['title'], 'outlets': outlets, 'links': c['items'], 'fresh_h': fresh})
    out.sort(key=lambda c: (-len(c['outlets']), c['fresh_h'] if c['fresh_h'] is not None else 99))
    return out[:60]


EMBEDS = [(r'(?:twitter|x)\.com/(\w+)/status/(\d+)', 'https://x.com/{}/status/{}'),
          (r'instagram\.com/(?:reel|p)/([\w-]{6,})', 'https://www.instagram.com/reel/{}/'),
          (r'(?:youtube\.com/embed/|youtu\.be/)([\w-]{11})', 'https://www.youtube.com/watch?v={}')]


def _embeds(h):
    """The viral post the article is about, as embedded in the page (MEASURED 10-31: 7 of 9 TV9/ABP articles embed
    an X, Instagram or YouTube video)."""
    found = []
    for rx, fmt in EMBEDS:
        for m in re.finditer(rx, h):
            found.append((m.start(), fmt.format(*m.groups())))
    return list(dict.fromkeys(u for _, u in sorted(found)))


def _article(url):
    """The article text from the page's JSON-LD articleBody (MEASURED 10-31 on TV9, India TV, ABP, News18: 2,100-5,000
    clean Hindi characters), paragraphs only as a fallback. Live test 3: the paragraph reader returned headline + CSS
    code for 3 stories, which passed a length check - so only text with 600+ Hindi letters counts."""
    import html
    try:
        h = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'}), timeout=20).read().decode('utf-8', 'ignore')
    except (urllib.error.URLError, OSError, ValueError):
        return '', []
    body = ''
    for blk in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(blk.strip())
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else d.get('@graph', [d])):
            if isinstance(x, dict) and isinstance(x.get('articleBody'), str) and len(x['articleBody']) > len(body):
                body = html.unescape(re.sub(r'<[^>]+>', ' ', x['articleBody']))
    if len(body) < 400:
        paras = [html.unescape(re.sub(r'<[^>]+>', '', p_)).strip() for p_ in re.findall(r'<p[^>]*>(.*?)</p>', h, re.S)]
        body = ' '.join(p_ for p_ in paras if len(p_) > 60)
    body = ' '.join(body.split())[:4000]
    return (body if sum(1 for ch in body if '\u0900' <= ch <= '\u097f') >= 600 else ''), _embeds(h)


def articles(cluster):
    out, seen = [], set()
    for it in cluster['links']:
        if it['outlet'] in seen:
            continue
        text, embeds = _article(it['url'])
        if text:
            seen.add(it['outlet'])
            out.append({'outlet': it['outlet'], 'url': it['url'], 'title': it['title'], 'text': text, 'embeds': embeds})
    return out


def pick(recent):
    cands = candidates()
    lines = '\n'.join('{}. ({} outlets) {}'.format(i, len(c['outlets']), c['title']) for i, c in enumerate(cands))
    out = ai.gemini(PICK.format(cands=lines, recent='\n'.join('- ' + r for r in recent[-40:]) or '- none'), temperature=0.4, chain='bulk')
    picks = []
    for p in out.get('picks') or []:
        if isinstance(p, dict) and isinstance(p.get('n'), int) and 0 <= p['n'] < len(cands) and p.get('query'):
            p['title'], p['cluster'] = cands[p['n']]['title'], cands[p['n']]
            if p.get('query_hi'):
                hindi.HI_QUERY[p['query']] = p['query_hi']
            picks.append(p)
    return picks


def script(outlets):
    lo, hi = WORDS
    prompt = SCRIPT.format(lo=lo, hi=hi, sources=ai.sources_text(outlets))
    problems = None
    for _ in range(3):
        s = ai.gemini(prompt + ('\n\nYOUR LAST VERSION WAS REJECTED FOR: %s. Fix exactly that.' % '; '.join(problems) if problems else ''))
        if s.get('skip'):
            return s
        problems = []
        teaser = [t for t in s.get('teaser') or [] if isinstance(t, dict) and isinstance(t.get('say'), str) and t['say'].strip()]
        story = [x for x in s.get('story') or [] if isinstance(x, str) and x.strip()]
        words = sum(len(t['say'].split()) for t in teaser) + sum(len(x.split()) for x in story)
        if not 4 <= len(teaser) <= 5:
            problems.append('%d teaser lines, need 4-5' % len(teaser))
        if not lo <= words <= hi:
            problems.append('%d spoken words, need %d-%d' % (words, lo, hi))
        if len([h for h in s.get('headline') or [] if isinstance(h, str) and h.strip()]) != 4:
            problems.append('headline must be exactly 4 lines')
        tags = [t for t in s.get('hashtags') or [] if isinstance(t, str) and t.strip()]
        if not s.get('title') or len(tags) < 4:
            problems.append('missing the Hindi title or fewer than 4 English hashtags')
        if not problems:
            s['teaser'], s['story'] = teaser, story
            return s
        log('  script rejected: ' + '; '.join(problems))
    return {'skip': 'script: ' + '; '.join(problems)}


def _fetch(m, dur, out):
    try:
        if m['kind'] == 'clip':
            f = moments.cut(m['vid'], m['t'], dur + 0.4, out + '.mp4')
        else:
            f = hindi.photo(m['url'], out + '.jpg')
        if f and moments.text_heavy(f):
            log('  refused (text on screen): %s' % m.get('desc', '')[:60])
            os.remove(f)
            return None
        return f
    except (OSError, subprocess.SubprocessError) as e:
        log('  picture dropped: %s' % str(e)[:80])
        return None


def story_clips(outlets, work):
    """The story's own viral video from the articles' embeds (X / Instagram / YouTube: the server fetches all three
    without a login, tested 10-31), in 8.4 s pieces 7+ s apart - what his videos show most. Live test 5 (10-31): three
    small viral stories found only 0-6 pictures on YouTube + Bing, so all three were skipped."""
    out = []
    for k, u in enumerate(list(dict.fromkeys(e for o in outlets for e in o.get('embeds', [])))[:3]):
        src = os.path.join(work, 'story%d.mp4' % k)
        try:
            r = moments._yt(['-f', 'bv*+ba/b', '-S', 'res:720', '--merge-output-format', 'mp4', '--max-filesize', '150M',
                             '--download-sections', '*0-120', '--match-filter', 'duration<=?900 & !is_live', '-o', src, u], timeout=240)
        except subprocess.TimeoutExpired:
            r = None
        if not os.path.exists(src):
            log('  story video not fetched: %s %s' % (u, ((r and r.stderr) or '').strip()[-120:]))
            continue
        d = _dur(src)
        step = max(7.0, (d - 3) / 8)
        t = 0.0
        while t < max(1.0, d - 3):
            piece = os.path.join(work, 'story%d_%02d.mp4' % (k, int(t)))
            # stream copy (starts on the keyframe before t): live test 6 re-encoded 8 pieces of a 5-min embed in 7.5 min
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', '%.2f' % t, '-i', src, '-t', '8.4', '-c', 'copy',
                            '-avoid_negative_ts', 'make_zero', piece])
            if os.path.exists(piece) and _dur(piece) > 1.0:
                out.append(({'kind': 'clip', 'desc': 'the viral video itself (%s)' % urllib.parse.urlparse(u).netloc}, piece))
            t += step
        os.remove(src)
    return out


def _frame(path, out, at=0.5):
    if path.endswith('.jpg'):
        shutil.copy(path, out)
    else:
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(at), '-i', path, '-frames:v', '1', out], check=True)
    return out


def _check(files, story, names, work):
    """One vision look at all story pictures (16:9 contact sheet); returns indexes to replace."""
    from PIL import Image, ImageDraw
    thumbs = [_frame(f, os.path.join(work, 'chk%03d.jpg' % i)) for i, f in enumerate(files)]
    w, h, cols = 320, 178, 6
    sheet = Image.new('RGB', (w * cols, (h + 24) * ((len(thumbs) + cols - 1) // cols)), 'white')
    d = ImageDraw.Draw(sheet)
    for i, t in enumerate(thumbs):
        x, y = (i % cols) * w, (i // cols) * (h + 24)
        sheet.paste(Image.open(t).convert('RGB').resize((w, h)), (x, y + 24))
        d.text((x + 4, y + 4), str(i), fill='black')
    p = os.path.join(work, 'check.jpg')
    sheet.save(p, quality=85)
    try:
        out = ai.gemini_vision(CHECK.format(story=story, names=', '.join(names)), [p])
    except ai.AIUnavailable:
        return []
    return [b for b in (out.get('bad') or []) if isinstance(b, int) and 0 <= b < len(files)]


# ---- one video ---------------------------------------------------------------------------------------------------------
def build(p, work):
    t0 = time.time()
    stage = lambda s: log('  [%4.0f s] %s' % (time.time() - t0, s))
    outlets = articles(p['cluster'])
    # viral human-interest stories are mostly carried by one of the 4 mainstream outlets first; one real article is
    # enough (the video ends with his "based on social media and media reports" line)
    if not outlets:
        return {'skip': 'no readable article'}
    s = script(outlets)
    if s.get('skip'):
        return s
    names = [n for n in (s.get('names') or p.get('names') or []) if isinstance(n, str)][:4]
    stage('script: %d teaser + %d story sentences' % (len(s['teaser']), len(s['story'])))
    found = moments.find(p['title'], names, p['query'], os.path.join(work, 'm'), want=48)
    stage('pictures found: %d' % len(found))
    # every picture is downloaded and checked BEFORE the voice is made (live test 10-31: a 3.7-min voice was made, then
    # the pictures ran out at shot 3). Clips are cut 8.4 s long so they fit any slot; photos as they are.
    story = story_clips(outlets, work)
    stage('story video pieces: %d' % len(story))
    # fetched in batches only until there are enough (live test 6: each YouTube picture costs ~1.5 min of the free
    # server's CPU, and fetching all 30 took 13+ min although ~12 were needed)
    if story:  # its own video shows the real people; a found picture NAMING them may be someone else with that name
        # (live test 7: Bing's "Aditya Singh" was a TV actor) - pictures of places, cars, objects stay (test 8 without
        # them: 36 shots of one man talking)
        who = {w.lower() for n in names for w in re.findall(r'[A-Za-z]{3,}', n)}
        found = [m for m in found if not who & set(re.findall(r'[a-z]{3,}', m.get('desc', '').lower()))]
    pool, rest, bad = [], list(enumerate(found[:30])), set()
    while rest and len(story) + len(pool) < ENOUGH:
        batch = []
        while rest and len(story) + len(pool) + len(batch) < ENOUGH + 4:  # +4 spare: the check refuses some
            k, m = rest.pop(0)
            f = _fetch(m, 8.0, os.path.join(work, 'src%03d' % k))
            if f:
                batch.append((m, f))
        no = set(_check([f for _, f in batch], p['title'], names, work)) if batch else set()
        bad |= {f for j, (_, f) in enumerate(batch) if j in no}
        pool += [x for j, x in enumerate(batch) if j not in no]
    # the story's own video skips the check: it IS the story, captions and all (live test 6: the check refused all 8
    # pieces of the Instagram reel an article was about, for its burned-in captions)
    pool = story + pool
    stage('usable pictures: %d (%d failed the picture check)' % (len(pool), len(bad)))
    if len(pool) < MIN_PICS:
        return {'skip': 'only %d usable pictures' % len(pool)}
    sents = [t['say'] for t in s['teaser']] + s['story']
    wav, cuts = voice(sents, work)
    total = cuts[-1]
    stage('voice: %.0f s' % total)
    nt = len(s['teaser'])
    # story shots: one per sentence, two when a sentence is long (his median shot is 6.6 s)
    slots = []
    for i in range(nt, len(sents)):
        a, b = cuts[i], cuts[i + 1]
        if b - a > MAX_SHOT:
            slots += [(i, a, (a + b) / 2), (i, (a + b) / 2, b)]
        else:
            slots.append((i, a, b))
    descs = '\n'.join('%d: %s %s' % (k, m['kind'], m.get('desc', '')) for k, (m, _) in enumerate(pool))
    try:
        pk = ai.gemini(MATCH.format(story=p['title'], sents='\n'.join('%d: %s' % (k, sents[i]) for k, (i, _, _) in enumerate(slots)),
                                    pics=descs, n=len(slots)), temperature=0.2).get('pick') or []
    except ai.AIUnavailable:
        pk = []
    order = [x if isinstance(x, int) and 0 <= x < len(pool) else k % len(pool) for k, x in enumerate(pk[:len(slots)])]
    order += [k % len(pool) for k in range(len(order), len(slots))]
    uses, files, starts = {}, [], []
    cap = max(MAX_USE, -(-len(slots) // len(pool)))  # few pictures (a small viral story): each shows more often
    for (i, a, b), x in zip(slots, order):
        if uses.get(x, 0) >= cap:  # picked too often: the least-used picture instead
            x = min(range(len(pool)), key=lambda k: uses.get(k, 0))
        f = pool[x][1]
        clip = _dur(f) if f.endswith('.mp4') else 0.0
        # a clip shown again starts further in, so the same seconds are never on screen twice
        starts.append(round((uses.get(x, 0) * 3.0) % max(0.1, clip - (b - a)), 2) if clip > (b - a) else 0.0)
        uses[x] = uses.get(x, 0) + 1
        files.append(f)
    stage('%d shots from %d different pictures' % (len(files), len(set(files))))
    # hook cards: the story's people - its own video first (the real person), else photos, one per teaser line
    people = ([f for _, f in story] or [f for f in dict.fromkeys(files) if f.endswith('.jpg')] or list(dict.fromkeys(files)))
    cards = []
    for j, t in enumerate(s['teaser']):
        img = os.path.join(work, 'card%d.jpg' % j)
        _frame(people[j % len(people)], img, at=1.0)
        cards.append({'img': os.path.basename(img), 'lines': [t['say'].strip()]})  # his card = the spoken line itself
    # one burst of a clip's own sound (his first video: ~3 s at 0:56), after the 3rd story sentence, from a speaking clip
    burst = None
    speak = [k for k, m in enumerate(found) if m['kind'] == 'clip' and hindi.SPEAKING.search(m.get('desc', ''))]
    if speak:
        bf = moments.cut(found[speak[0]]['vid'], found[speak[0]]['t'], 3.2, os.path.join(work, 'burst.mp4'))
        if bf and not moments.text_heavy(bf):
            after = next((k for k, sl in enumerate(slots) if sl[0] >= nt + 3), None)
            if after is not None:
                burst = {'src': 'burst.mp4', 'start': 0.3, 'dur': 2.5, 'after_shot': nt + after, 'at': slots[after][2]}
    shots = [{'card': j, 'dur': cuts[j + 1] - cuts[j]} for j in range(nt)]
    for (i, a, b), f, st in zip(slots, files, starts):
        shots.append({'src': os.path.basename(f), 'start': st, 'dur': b - a, 'still': f.endswith('.jpg')})
    spec = {'type': 'viral', 'headline': [h.strip().rstrip(',').strip() for h in s['headline']][:4], 'cards': cards, 'shots': shots,
            'burst': burst, 'hits': cuts[:nt], 'total': total}
    job = os.path.join(work, 'job.json')
    json.dump(spec, open(job, 'w'), ensure_ascii=False)
    tar = os.path.join(work, 'in.tar.gz')
    with tarfile.open(tar, 'w:gz') as tf:
        for f in set(files) | {os.path.join(work, c['img']) for c in cards}:
            tf.add(f, arcname=os.path.basename(f))
        if burst:
            tf.add(os.path.join(work, 'burst.mp4'), arcname='burst.mp4')
        tf.add(wav, arcname='voice.wav')
        tf.add(SFX, arcname='sfx.wav')
        tf.add(job, arcname='job.json')
    video = os.path.join(work, 'video.mp4')
    marks = cloud.render(tar, video) or cloud.render(tar, video)
    if not marks:
        raise RuntimeError('Cloud Run render failed twice')
    stage('rendered on Cloud Run (%s), %.0f s video' % (marks, _dur(video)))
    # the post text exactly like his: Hindi title + English hashtags (user 10-31)
    tags = ['#' + re.sub(r'[^a-z0-9]', '', t.lower().lstrip('#')) for t in s['hashtags'] if isinstance(t, str)]
    tags = list(dict.fromkeys([t for t in tags if len(t) > 2] + ['#viralvideo', '#trending']))
    post = '%s %s' % (s['title'].strip(), ' '.join(tags))
    return {'video': video, 'title': post, 'caption': post, 'headline': ' '.join(spec['headline']),
            'seconds': round(_dur(video)), 'shots': len(shots)}


def build_one():
    """Called by the builder timer: one video whenever fewer than BUFFER are ready (any hour; the publisher paces them)."""
    if len(ready()) >= BUFFER:
        return
    from . import dedupe
    hist = _load('viral_posted.json', [])
    failed = {q: t for q, t in _load('viral_failed.json', {}).items() if schedule.now() - datetime.fromisoformat(t) < timedelta(hours=24)}
    recent = [h['title'] for h in hist[-40:]] + [r['title'] for r in ready()]
    try:
        picks = pick(recent + list(failed))
    except ai.AIUnavailable as e:
        log('  viral build paused, AI unavailable: %s' % str(e)[:150])
        return
    for p in picks:
        if any(dedupe._overlap(dedupe.words(p['query']), dedupe.words(q)) >= dedupe.LIMIT for q in failed):
            continue
        work = os.path.join(os.path.dirname(QUEUE), 'viral_work', time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(work, exist_ok=True)
        log('VIRAL story: %s' % p['title'][:100])
        try:
            res = build(p, work)
        except ai.AIUnavailable as e:
            log('  viral build paused, AI unavailable: %s' % str(e)[:150])
            return
        except Exception as e:  # noqa: BLE001 - one story never stops the builder
            import traceback
            traceback.print_exc()
            res = {'skip': 'error %s: %s' % (type(e).__name__, str(e)[:120])}
        if res.get('skip'):
            log('  viral skip (%s): %s' % (res['skip'], p['query']))
            failed[p['query']] = schedule.now().isoformat()
            schedule._save('viral_failed.json', failed)
            shutil.rmtree(work, ignore_errors=True)
            continue
        dest = os.path.join(QUEUE, os.path.basename(work))
        os.makedirs(dest, exist_ok=True)
        shutil.move(res['video'], os.path.join(dest, 'video.mp4'))
        json.dump({'built': schedule.now().isoformat(), 'title': res['title'], 'caption': res['caption'], 'query': p['query'],
                   'seconds': res['seconds'], 'shots': res['shots']}, open(os.path.join(dest, 'meta.json'), 'w'), ensure_ascii=False, indent=1)
        shutil.rmtree(work, ignore_errors=True)
        log('VIRAL READY %s (%d s, %d shots)' % (res['title'][:80], res['seconds'], res['shots']))
        return


# ---- publishing --------------------------------------------------------------------------------------------------------
def plan():
    """5 random times a day, one inside each IST window, seeded by the day (a lost file gives the same times)."""
    day = now_ist().date()
    p = _load('viral_schedule.json', {})
    if p.get('day') != day.isoformat() or len(p.get('slots', [])) != len(WINDOWS):
        rng = random.Random('viral-%s' % day.isoformat())
        start = datetime(day.year, day.month, day.day, tzinfo=IST)
        p = {'day': day.isoformat(), 'slots': [(start + timedelta(minutes=rng.uniform(a, b))).isoformat() for a, b in WINDOWS]}
        schedule._save('viral_schedule.json', p)
    return p


def _upload(page, token, video, title, description):
    import uuid
    b = uuid.uuid4().hex
    fields = {'title': title[:250], 'description': description, 'published': 'true', 'access_token': token}
    head = b''.join(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode() for k, v in fields.items())
    head += ('--%s\r\nContent-Disposition: form-data; name="source"; filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n' % b).encode()
    body = head + open(video, 'rb').read() + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request('https://graph-video.facebook.com/v26.0/%s/videos' % page, data=body,
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + b})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read()).get('id')


def _on_page(page, token, caption):
    """Is a video with this exact text already on the Page? (an upload whose answer was lost). None = cannot tell."""
    url = 'https://graph.facebook.com/v26.0/%s/videos?fields=description&limit=10&access_token=%s' % (page, urllib.parse.quote(token))
    try:
        rows = json.loads(urllib.request.urlopen(url, timeout=60).read()).get('data') or []
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return any((r.get('description') or '').strip() == caption.strip() for r in rows)


def _mark(v, **kw):
    m = os.path.join(v['dir'], 'meta.json')
    meta = dict(json.load(open(m)), **kw)
    json.dump(meta, open(m, 'w'), ensure_ascii=False, indent=1)


def _posted(v, vid):
    hist = _load('viral_posted.json', [])
    schedule._save('viral_posted.json', (hist + [{'time': schedule.now().isoformat(), 'title': v['title'], 'id': vid}])[-300:])
    shutil.rmtree(v['dir'], ignore_errors=True)  # published and deleted
    log('VIRAL PUBLISHED %s %s' % (vid, v['title'][:80]))


def publish_due():
    """Posts by COUNT, not by slot: whenever fewer have gone out today than slots have passed, the next ready video
    goes (2 h apart on schedule, 75 min when catching up), so a late build or a refused upload never costs a post.
    No double posts: an upload with no answer is checked on the Page before it is ever sent again."""
    q = ready()
    if not q or not POST_HOURS[0] <= now_ist().hour < POST_HOURS[1]:
        return
    now, n = schedule.now(), len(posted_today())
    due = sum(1 for s_ in plan()['slots'] if datetime.fromisoformat(s_) <= now)
    if n >= min(due, PER_DAY):
        return
    hist = _load('viral_posted.json', [])
    gap = timedelta(hours=2) if n == due - 1 else timedelta(minutes=75)
    if hist and now - datetime.fromisoformat(hist[-1]['time']) < gap:
        return
    page, token = os.environ['FB_PAGE_ID'], os.environ['FB_PAGE_TOKEN']
    v = q[0]
    if v.get('attempted'):  # its last upload ended without an answer
        seen = _on_page(page, token, v['caption'])
        if seen:
            return _posted(v, None)
        if seen is None or now - datetime.fromisoformat(v['attempted']) < timedelta(minutes=30):
            return  # Facebook may still be processing it: wait, never risk a second copy
        log('VIRAL: earlier upload is not on the Page after 30 min, sending again')
    _mark(v, attempted=now.isoformat())
    try:
        vid = _upload(page, token, os.path.join(v['dir'], 'video.mp4'), v['title'], v['caption'])
    except urllib.error.HTTPError as e:  # Facebook refused it: nothing went out
        fails = v.get('fb_errors', 0) + 1
        log('VIRAL FACEBOOK ERROR %d/3: HTTP %s %s' % (fails, e.code, e.read().decode('utf-8', 'ignore')[:300]))
        if fails >= 3:
            log('VIRAL: dropped after 3 refusals, the builder makes another')
            shutil.rmtree(v['dir'], ignore_errors=True)
        else:
            _mark(v, attempted=None, fb_errors=fails)
        return
    except (urllib.error.URLError, OSError) as e:  # unknown end: checked on the Page next run, before any resend
        log('VIRAL UPLOAD ENDED UNKNOWN (%s): checking the Page before any retry' % str(e)[:150])
        return
    _posted(v, vid)
