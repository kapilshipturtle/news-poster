"""Indian Daily News long videos (user 2026-10-31: "yes go ahead and keep this ... video length 3 minute +"): 2-3 a day,
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
import shutil
import struct
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import ai, cloud, hindi, moments, schedule, sources

IST = ZoneInfo('Asia/Kolkata')
QUEUE = os.path.join(schedule.STATE, 'viral_queue')
PER_DAY = 3
WINDOWS = [(9 * 60, 11 * 60 + 30), (14 * 60, 16 * 60 + 30), (19 * 60, 21 * 60 + 30)]  # IST, one video in each
VOICE_DIR = os.environ.get('VIRAL_VOICE_DIR', '/home/poster/voices/shiva-hindi')
SFX = os.environ.get('VIRAL_SFX', '/home/poster/sfx/viral_hit.wav')  # Meta SC whoosh_2 + impact_1 -6 dB (measured match)
WORDS = (480, 650)        # the clone reads ~158 words/min (measured 10-31) -> about 3:00-4:10 like his 2:40-4:17
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
Use ONLY facts in the SOURCES. Never invent quotes, numbers, names or events. If the sources are too thin, return
{{"skip": "<reason>"}}.

STRUCTURE (this is what makes people watch to the end):
1. "teaser": 4 or 5 punchy spoken lines (8-16 words each) BEFORE the story. Each line is a new claim. Use the patterns:
   withheld detail ("... ने लगाया ऐसा आरोप कि ..." - never say WHAT yet), contradiction ("कभी ... आज ...",
   "न ... न ...", "जिसे ... उसी ने ..."), emotion ("फूट-फूट कर रोने लगी"). The LAST teaser line asks the big question and
   promises the answer: e.g. "आखिर ... ? सब देखिए।" Each teaser line also has "card": the same idea in 2-4 very short
   on-screen lines (max 14 characters each).
2. "story": 30-45 spoken sentences (each max 20 words). Starts with "दरअसल,". Tell it in time order with real details
   and the emotions. Around the middle, re-open the question ("अब ये जानना बहुत ज़रूरी है कि ..."). Keep the answer to the
   teaser question for the LAST 20% of the story. End with: "आपको क्या लगता है? कमेंट कर प्रतिक्रिया ज़रूर दीजिएगा।" and
   "दी गई जानकारी सोशल मीडिया और मीडिया रिपोर्ट्स पर आधारित है।"
Total spoken words (teaser + story): {lo} to {hi}. Simple spoken Hindi like a TV anchor; numbers as Hindi words.
"headline": ONE sentence split into 4 lines of max 16 characters (it stays on screen the whole video): lines 1-2 are the
setup, lines 3-4 the payoff, line 3 ends with ".." (e.g. "जिसे घर में रखा" / "उसी मेहमान ने" / "लगाया आरोप.." / "रो पड़ीं शिवानी").
"title": the Facebook title, Hindi, max 110 characters, hook + contradiction (like the teaser), no hashtags.
"caption": 2 Hindi sentences that make people watch, then 4 hashtags.

Return JSON: {{"teaser": [{{"say": "...", "card": ["..", ".."]}}], "story": ["...", "..."], "headline": ["..", "..", "..", ".."],
"title": "...", "caption": "...", "names": ["<people, English>"]}}

SOURCES:
{sources}"""

MATCH = """Pick a picture for each spoken sentence of a Hindi news video about: {story}
SENTENCES:
{sents}
PICTURES (number: what it shows):
{pics}
Use each picture at most twice; prefer the picture that shows who or what the sentence talks about; spread them out.
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
            if schedule.now() - datetime.fromisoformat(meta['built']) > timedelta(hours=10):
                shutil.rmtree(os.path.join(QUEUE, d), ignore_errors=True)  # stale news
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
def pick(recent):
    sources.MAX_AGE_H = 24  # people stories trend for a day, not six hours
    cands = sources.trending()[:60]
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
        teaser = [t for t in s.get('teaser') or [] if isinstance(t, dict) and t.get('say') and t.get('card')]
        story = [x for x in s.get('story') or [] if isinstance(x, str) and x.strip()]
        words = sum(len(t['say'].split()) for t in teaser) + sum(len(x.split()) for x in story)
        if not 4 <= len(teaser) <= 5:
            problems.append('%d teaser lines, need 4-5' % len(teaser))
        if not lo <= words <= hi:
            problems.append('%d spoken words, need %d-%d' % (words, lo, hi))
        if len([h for h in s.get('headline') or [] if isinstance(h, str) and h.strip()]) != 4:
            problems.append('headline must be exactly 4 lines')
        if not s.get('title') or not s.get('caption'):
            problems.append('missing title or caption')
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
    outlets, _ = sources.articles(p['cluster'], p['query'])
    if len(outlets) < 2:
        return {'skip': 'only %d outlets' % len(outlets)}
    s = script(outlets)
    if s.get('skip'):
        return s
    names = [n for n in (s.get('names') or p.get('names') or []) if isinstance(n, str)][:4]
    stage('script: %d teaser + %d story sentences' % (len(s['teaser']), len(s['story'])))
    found = moments.find(p['title'], names, p['query'], os.path.join(work, 'm'), want=48)
    stage('pictures found: %d' % len(found))
    if len(found) < 12:
        return {'skip': 'only %d usable pictures' % len(found)}
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
    descs = '\n'.join('%d: %s %s' % (k, m['kind'], m.get('desc', '')) for k, m in enumerate(found))
    try:
        pk = ai.gemini(MATCH.format(story=p['title'], sents='\n'.join('%d: %s' % (k, sents[i]) for k, (i, _, _) in enumerate(slots)),
                                    pics=descs, n=len(slots)), temperature=0.2).get('pick') or []
    except ai.AIUnavailable:
        pk = []
    order = [x if isinstance(x, int) and 0 <= x < len(found) else k % len(found) for k, x in enumerate(pk[:len(slots)])]
    order += [k % len(found) for k in range(len(order), len(slots))]
    # fetch (reusing a fetched picture when the same one is picked again), spares replace refusals
    got, files, used = {}, [], set(order)
    spare = [k for k in range(len(found)) if k not in used]
    for k, ((i, a, b), m) in enumerate(zip(slots, order)):
        f = got.get(m)
        while not f:
            f = _fetch(found[m], max(b - a, 4.0), os.path.join(work, 'src%03d' % k))
            if f:
                got[m] = f
            elif spare:
                m = spare.pop(0)
            else:
                return {'skip': 'ran out of usable pictures at shot %d of %d' % (k + 1, len(slots))}
        files.append(f)
    stage('fetched %d shots (%d different pictures)' % (len(files), len(set(files))))
    for k in _check(files, p['title'], names, work):
        while spare:
            m = spare.pop(0)
            f = _fetch(found[m], max(slots[k][2] - slots[k][1], 4.0), os.path.join(work, 'r%03d' % k))
            if f:
                files[k] = f
                log('  replaced shot %d after the picture check' % k)
                break
    # hook cards: the story's people (photos first), one per teaser line
    people = [f for f in dict.fromkeys(files) if f.endswith('.jpg')] or list(dict.fromkeys(files))
    cards = []
    for j, t in enumerate(s['teaser']):
        img = os.path.join(work, 'card%d.jpg' % j)
        _frame(people[j % len(people)], img, at=1.0)
        cards.append({'img': os.path.basename(img), 'lines': [x for x in t['card'] if isinstance(x, str)][:4] or [t['say'][:14]]})
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
    for (i, a, b), f in zip(slots, files):
        shots.append({'src': os.path.basename(f), 'start': 0.0, 'dur': b - a, 'still': f.endswith('.jpg')})
    spec = {'type': 'viral', 'headline': [h.strip() for h in s['headline']][:4], 'cards': cards, 'shots': shots,
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
    return {'video': video, 'title': s['title'].strip(), 'caption': s['caption'].strip(), 'headline': ' '.join(spec['headline']),
            'seconds': round(_dur(video)), 'shots': len(shots)}


def build_one():
    """Called by the builder timer: one video when the day's 3 are not yet covered by posted + ready."""
    n_ready, n_posted = len(ready()), len(posted_today())
    hour = now_ist().hour
    if n_ready >= 1 or n_ready + n_posted >= PER_DAY or not 6 <= hour < 21:
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
    """3 random times a day, one inside each IST window, seeded by the day (a lost file gives the same times)."""
    day = now_ist().date()
    p = _load('viral_schedule.json', {})
    if p.get('day') != day.isoformat():
        rng = random.Random('viral-%s' % day.isoformat())
        start = datetime(day.year, day.month, day.day, tzinfo=IST)
        p = {'day': day.isoformat(), 'slots': [(start + timedelta(minutes=rng.uniform(a, b))).isoformat() for a, b in WINDOWS], 'done': []}
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


def publish_due():
    p, q = plan(), ready()
    due = [s for s in p['slots'] if s not in p['done'] and datetime.fromisoformat(s) <= schedule.now()]
    if not (due and q) or len(posted_today()) >= PER_DAY:
        return
    hist = _load('viral_posted.json', [])
    if hist and schedule.now() - datetime.fromisoformat(hist[-1]['time']) < timedelta(hours=2):
        return
    v = q[0]
    p['done'].append(due[0])
    schedule._save('viral_schedule.json', p)  # marked first: a video is never posted twice
    try:
        vid = _upload(os.environ['FB_PAGE_ID'], os.environ['FB_PAGE_TOKEN'], os.path.join(v['dir'], 'video.mp4'), v['title'],
                      '%s\n\n%s' % (v['title'], v['caption']))
    except urllib.error.HTTPError as e:  # Facebook refused it: nothing went out, the video stays for the next time
        log('VIRAL FACEBOOK ERROR: HTTP %s %s' % (e.code, e.read().decode('utf-8', 'ignore')[:300]))
        return
    except (urllib.error.URLError, OSError) as e:  # unknown end: dropped, never retried (no double post)
        log('VIRAL UPLOAD ENDED UNKNOWN (%s): dropped' % str(e)[:150])
        shutil.rmtree(v['dir'], ignore_errors=True)
        return
    schedule._save('viral_posted.json', (hist + [{'time': schedule.now().isoformat(), 'title': v['title'], 'id': vid}])[-300:])
    shutil.rmtree(v['dir'], ignore_errors=True)  # published and deleted
    log('VIRAL PUBLISHED %s %s' % (vid, v['title'][:80]))
