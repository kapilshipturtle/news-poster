"""PROFILE=hi: the Hindi Page (Indian Daily News). Same engine as the US Page; this module, imported by run.py before
anything runs, switches what differs. The US Page never imports it.
User decisions (2026-10-23/24): Hindi Reels only, 12 a day; Fish voice "शांत हिंदी पुरुष आवाज़" on the free model; most
viral Indian stories; Hindi + English searches; pictures fill the phone screen (vertical sources first, wide ones cropped
around the subject); no captions or other text on screen; strict picture checks (measured on the test Reels: one-frame
English-only OCR let an app advert, news banners, subtitles and a Punjabi banner through)."""
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

from . import ai, moments, net, reel, sources

FISH_VOICE = os.environ.get('HINDI_VOICE', '6a9e3e125639485bbe404363818ef997')  # fish-MA, chosen by the user
FISH_MODEL = 's2.1-pro-free'  # Fish's free model (same model as paid s2.1-pro; Channel Studio uses it)
FISH_KEY = os.path.expanduser(os.environ.get('FISH_KEY_FILE', '~/.fish_api_key'))


def load_env():
    """The shared secrets, then the Hindi Page's own id/token/name in place of the US Page's."""
    net.load_env()
    for k in ('ID', 'TOKEN', 'NAME'):
        if os.environ.get('FB_PAGE3_' + k):
            os.environ['FB_PAGE_' + k] = os.environ['FB_PAGE3_' + k]


sources.FEEDS = {
    'NDTV': 'https://feeds.feedburner.com/ndtvnews-top-stories',
    'NDTV India': 'https://feeds.feedburner.com/ndtvkhabar-latest',
    'Times of India': 'https://timesofindia.indiatimes.com/rssfeedstopstories.cms',
    'Hindustan Times': 'https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml',
    'Indian Express': 'https://indianexpress.com/section/india/feed/',
    'The Hindu': 'https://www.thehindu.com/news/national/feeder/default.rss',
    'India Today': 'https://www.indiatoday.in/rss/1206578',
    'News18': 'https://www.news18.com/rss/india.xml',
    'BBC Hindi': 'https://feeds.bbci.co.uk/hindi/rss.xml',
    'Aaj Tak': 'https://www.aajtak.in/rssfeeds/?id=home',
    'Mint': 'https://www.livemint.com/rss/news',
    'Firstpost': 'https://www.firstpost.com/commonfeeds/v1/mfp/rss/india.xml',
    'Economic Times': 'https://economictimes.indiatimes.com/news/india/rssfeeds/81582957.cms',
    'Business Standard': 'https://www.business-standard.com/rss/home_page_top_stories.rss',
    'Free Press Journal': 'https://www.freepressjournal.in/stories.rss',
}  # MEASURED 2026-10-23/24: all answer with fresh items AND readable articles, except NDTV's articles (HTTP 403, kept for
# spotting what's trending). Failed: Navbharat/Jagran/Outlook 404, Zee/The Week 403, Scroll/ThePrint bad XML, Deccan Herald 500
# user 2026-10-24: Hindi news = only the last 6 hours ("24 hours will not work for Hindi"); undated items can't be proven fresh
sources.MAX_AGE_H, sources.REQUIRE_DATE = 6, True

ai.PICK = ai.PICK.replace(
    'You run a US Facebook news page. Choose the stories most likely to get Americans reacting',
    'You run an Indian Facebook news page that posts Hindi Reels. Choose the MOST VIRAL stories: the ones most likely to '
    'get Indians reacting').replace('how many US outlets', 'how many Indian outlets').replace(
    "non-US stories Americans won't care about", "stories Indians won't care about").replace(
    'money/tech everyone uses', 'money/tech everyone uses, cricket, Bollywood').replace(
    '"<4-8 word news search query for this exact story>"',
    '"<4-8 word ENGLISH news search query for this exact story>", "query_hi": "<4-8 word HINDI (Devanagari) search query '
    'for the same story>"').replace('NFL, NBA, MLB,', 'CRICKET, NFL, NBA, MLB,')
moments.SPORTS_TAGS = moments.SPORTS_TAGS | {'CRICKET'}  # sports stories use photos only (leagues claim hardest)
moments.STRICT = re.compile(moments.STRICT.pattern[:-3] + r'|bcci|ipl|icc|star sports|jiocinema|jiohotstar|hotstar|sony ?liv|'
                            r'sony sports|t-series|zee music|tips official|yrf|yash raj|dharma|saregama|eros)\b', re.I)
moments.WIDE_ONLY, moments.PREFER_VERTICAL = False, True
# MEASURED 2026-10-24 (Daler Mehndi story): 18 usable moments for 20 pieces -> more clips per story, and pieces up to
# 3.5 s (user: B-roll under 3-4 s) so a 50 s Reel needs ~15 pieces instead of ~19
moments.MAX_VIDEOS, moments.PER_VIDEO = 7, 3
# 2026-10-24 live: re-encoding 1080p cuts (--force-keyframes-at-cuts) on the e2-micro timed out at 300 s, 3 at a time,
# and loaded the VM the US Page shares -> cut on existing keyframes (stream copy: no encoding; a clip may start a
# couple of seconds early, and the 3-frame checks + final look judge what is really in it)
moments.CUT_EXTRA = []
reel.MAX_PIECE = 4.0  # user: B-roll under 3-4 s; the final check left too few spares at 3.5 s
moments.CHECK = moments.CHECK.replace('the picture editor of a US news page', 'the picture editor of an Indian news page').replace(
    'Return JSON: {{"<sheet letter>": {{"<number>": {{"keep": true|false, "desc": "what it shows, <= 12 words"}}}}}} for every number.',
    'ALSO REJECT (the final check refuses these, so they would only waste a spare): a TV graphic, show title card, '
    'breaking-news banner, split screen, video play button, or website watermark across the picture; a person who is not '
    'one of the story\'s people; a place, crowd or event unrelated to this story.\n'
    'Also give "x": where the main subject\'s centre is across the picture, 0 = left edge, 0.5 = middle, 1 = right edge '
    '(the picture will be cropped to a tall phone screen around it).\n'
    'Return JSON: {{"<sheet letter>": {{"<number>": {{"keep": true|false, "desc": "what it shows, <= 12 words", "x": 0.5}}}}}} '
    'for every number.')

# ---- Hindi searches next to the English ones (user: "Hindi + English searches") ----------------------------------------
HI_QUERY = {}
_pick, _yt_search, _bing = ai.pick_stories, moments.yt_search, moments.bing_images


def pick_stories(cands, recent):
    picks = _pick(cands, recent)
    for p in picks:
        if isinstance(p.get('query_hi'), str) and p['query_hi'].strip():
            HI_QUERY[p['query']] = p['query_hi'].strip()
    return picks


def yt_search(query, subject_words, n=10):
    out = _yt_search(query, subject_words, n)
    seen = {v['id'] for v in out}
    out += [v for v in _yt_search(query + ' ANI', subject_words, n) if v['id'] not in seen]  # agency footage of the event
    for v in out:
        v['own'] = v['own'] or bool(AGENCY.search(v['channel']))  # find() puts these first
    if query in HI_QUERY:
        seen = {v['id'] for v in out}
        out += [v for v in _yt_search(HI_QUERY[query], subject_words, n) if v['id'] not in seen]
    return out


def bing_images(query, n=24, tall=False):
    out = _bing(query, n, tall)
    if query in HI_QUERY:
        seen = {p['url'] for p in out}
        out += [p for p in _bing(HI_QUERY[query], n, tall) if p['url'] not in seen]
    return out


ai.pick_stories, moments.yt_search, moments.bing_images = pick_stories, yt_search, bing_images
_articles = sources.articles


def articles(cluster, query, want=3):
    """MEASURED 2026-10-24: NDTV's article pages answer HTTP 403, so many stories reported by 2+ outlets had only ONE
    readable article and were skipped. The story still counts as confirmed when 2+ outlets report it: the readable
    article is the fact source, and another outlet's own headline for the same story is the second, confirming source
    (never bypassing a site's block)."""
    outlets, ranked = _articles(cluster, query, want)
    if len(outlets) == 1:
        others = [i for i in cluster.get('links') or [] if i.get('outlet') and i['outlet'] != outlets[0]['outlet']]
        if others:
            o = others[0]
            outlets.append({'outlet': o['outlet'], 'url': o.get('url', ''), 'title': o.get('title', ''),
                            'text': '(headline only: the article page is not readable) ' + o.get('title', '')})
    return outlets, ranked


sources.articles = articles
_find = moments.find


# raw event footage from news agencies / official channels: real pictures with only a small logo (user 2026-10-24: "not
# able to find exact images and clips")
AGENCY = re.compile(r'(?i)\b(ani|ani news|dd news|doordarshan|pib|sansad tv|pmo|narendra modi|mea ?india|airnews|ians)\b')
PICTURES = {'n': None}
PICTURES_READY = threading.Event()


def find(story, names, query, work, want=24, clips_ok=True, used=()):
    """More spare moments (the strict checks refuse far more Indian news pictures than US ones), and the count is handed
    to the voice, which is only requested when the story has enough pictures (the free Hindi voice allows 10 a day)."""
    try:
        found = _find(story, names, query, work, want=max(want, 40), clips_ok=clips_ok, used=used)
        PICTURES['n'] = len(found)
        return found
    finally:
        PICTURES_READY.set()


moments.find = find

# ---- the script ----------------------------------------------------------------------------------------------------------
reel.SCRIPT = """You write the voice-over for a 45-60 second vertical news Reel for an Indian Facebook news page (the page name is not part of the story; never mention it).
Write it in natural spoken HINDI (Devanagari), the way a TV news anchor speaks: simple everyday Hindi; common English
words people really use (police, train, report) are fine, written in Devanagari.
The story is whatever the SOURCES below report. Use ONLY facts stated in the SOURCES. Never invent numbers, quotes,
names, scenes or outcomes. If the sources are too thin or contradict each other, return {{"skip": "<reason>"}}.

Return JSON: {{"headline": str, "lines": [str, ...], "screen_lines": [str, ...], "facts": [str, str], "names": [str], "caption": str}}
- lines: 10 to 22 spoken HINDI sentences, 130-260 words in total (45 seconds to 2 minutes; as long as the story's real facts
  carry, never padded), each at most 16 words. Numbers written as Hindi words for
  the ear ("चार", "पचास हज़ार"). Line 1 (max 10 words) is the hook: the most surprising or emotional REAL detail.
  Then what happened, why it matters to an ordinary Indian family, and the human stakes.
  The LAST line is one specific question people will disagree about, ending with "?" (never "comment करें", "like करें", "share करें").
- screen_lines: the same lines again (same count and order).
- headline: max 7 words in ENGLISH (an internal label).
- facts: two concrete details from the sources, max 9 words each, in Hindi.
- names: the people, places and organisations the story is about, spelled in ENGLISH the way Indian English news writes them (max 4).
- caption: the Reel's description in HINDI, 15-40 words, emotional but factual, no hashtags, no credits, no links.

SOURCES:
{sources}"""
_problems = reel.script_problems
ALLOWED = re.compile(r'[ऀ-ॿ A-Za-z0-9.,!?\'"()%:;\-₹–]')


def script_problems(s):
    """The US rules, plus: only Hindi, English, digits and punctuation (Flash-Lite once wrote Japanese katakana inside a
    Hindi word, which the voice would have read as noise)."""
    p = [x for x in _problems(s) if 'words, need' not in x and 'lines, need' not in x]
    if not 9 <= len(s.get('lines') or []) <= 24:
        p.append('{} lines, need 9-24'.format(len(s.get('lines') or [])))
    words = sum(len(l.split()) for l in s.get('lines') or [])
    # user 2026-10-24: "reels can be any size from 45 seconds to 2 minutes". MEASURED F1: ~2.4 words/s (38 words =
    # 15.6 s) -> 120-280 words = ~50-117 s (Fish, the fallback, is faster: ~2.9 words/s)
    if not 120 <= words <= 280:
        p.append('{} words, need 120-280'.format(words))
    bad = sorted({ch for ch in ' '.join(s.get('lines') or []) if not ALLOWED.match(ch)})
    if bad:
        p.append('characters that are not Hindi or English: {} (write those words in Devanagari)'.format(''.join(bad)))
    return p


reel.script_problems = script_problems
reel.SCRIPT_WORDS = '130 to 260'  # what a rejected Hindi script is told (it was told the US 120-150 and overshot)
reel.VOICE_SECONDS = (43, 128)  # user: 45 s to 2 min
ai.sources_text = lambda src, per=2500, total=10000: '\n\n'.join(
    '[{}] {}\n{}'.format(a['outlet'], a['title'], a['text'][:per]) for a in src)[:total]  # Devanagari costs more tokens


# ---- the voice: F1 (Google AI Studio), Fish as the last resort ----------------------------------------------------------
F1_VOICE = os.environ.get('HINDI_VOICE_F1', 'hi-in-podcaster-1')  # user 2026-10-24: "use F1-hi-in-podcaster-1"
F1_STYLE = 'a calm, confident Hindi TV news anchor: clear, natural and warm'  # a style field: never read aloud (measured)
# MEASURED: 10 free requests a day per model (429 "limit: 10 requests per day on Free Tier"); the same F1 voice also works
# on the Flash-Lite TTS model (16.8 s test, Whisper clean), which has its own 10
F1_MODELS = ('gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts')


def _audio(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if k in ('data', 'audio') and isinstance(v, str) and len(v) > 1000:
                return v
            a = _audio(v)
            if a:
                return a
    elif isinstance(d, list):
        for v in d:
            a = _audio(v)
            if a:
                return a
    return None


def _f1(text, out):
    """The whole script in ONE request (Interactions API, F1 library voice + style). None when both models' daily
    quotas are used up or fail; a 429 marks that model until Google's stated reset."""
    import base64
    skip = ai._exhausted()
    for model in F1_MODELS:
        if 'tts:' + model in skip:
            continue
        body = {'model': model, 'input': [{'type': 'user_input', 'content': [{'type': 'text', 'text': text, 'annotations': [
            {'type': 'speech_metadata', 'style': F1_STYLE}]}]}], 'response_format': {'type': 'audio'},
            'generation_config': {'speech_config': [{'voice': F1_VOICE}]}}
        req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions', data=json.dumps(body).encode(),
                                     headers={'x-goog-api-key': os.environ.get('GEMINI_API_KEY', ''), 'Content-Type': 'application/json'})
        try:
            a = _audio(json.loads(urllib.request.urlopen(req, timeout=300).read()))
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'ignore')
            print('  F1 voice on {} failed: HTTP {} {}'.format(model, e.code, txt[:160]), flush=True)
            if e.code == 429:
                m = re.search(r'retry in (\d+)h(\d+)m', txt)
                ai._mark_exhausted('tts:' + model, int(m.group(1)) * 3600 + int(m.group(2)) * 60 if m else 3600)
            continue
        except (urllib.error.URLError, OSError, ValueError) as e:
            print('  F1 voice on {} failed: {}: {}'.format(model, type(e).__name__, str(e)[:120]), flush=True)
            continue
        if a:
            open(out, 'wb').write(base64.b64decode(a))
            return out
    return None


def _fish(text, out):
    body = json.dumps({'text': text, 'reference_id': FISH_VOICE, 'format': 'wav', 'normalize': True, 'latency': 'normal',
                       'temperature': 0.7, 'top_p': 0.7, 'prosody': {'speed': 1.0}}).encode()  # user rule: speed always 1.0
    req = urllib.request.Request('https://api.fish.audio/v1/tts', data=body, headers={
        'Authorization': 'Bearer ' + open(FISH_KEY).read().strip(), 'Content-Type': 'application/json', 'model': FISH_MODEL})
    err = None
    for _ in range(2):
        try:
            open(out, 'wb').write(urllib.request.urlopen(req, timeout=300).read())
            return out
        except (urllib.error.URLError, OSError) as e:
            err = e
    raise ai.AIUnavailable('Fish voice: {}: {}'.format(type(err).__name__, str(err)[:150]))  # retried next run


def _duration(path):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                                capture_output=True, text=True).stdout)


def line_timing(wav, lines):
    """Each line's (start, end): the expected boundary by the share of letters, snapped to the nearest real pause."""
    err = subprocess.run(['ffmpeg', '-hide_banner', '-i', wav, '-af', 'silencedetect=noise=-35dB:d=0.18', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r'silence_start: ([\d.]+)', err)]
    ends = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', err)]
    pauses = [(a + b) / 2 for a, b in zip(starts, ends)]
    dur = _duration(wav)
    speech0 = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    trailing = starts and (len(ends) < len(starts) or ends[-1] >= dur - 0.05)  # ffmpeg may or may not close the last one
    speech1 = starts[-1] if trailing else dur
    n = [len(l) for l in lines]
    cuts, last = [speech0], speech0
    for i in range(1, len(lines)):
        want = speech0 + (speech1 - speech0) * sum(n[:i]) / sum(n)
        cand = [p for p in pauses if last + 0.8 < p < speech1 - 0.5]
        best = min(cand, key=lambda p: abs(p - want)) if cand else want
        cuts.append(best if abs(best - want) < 2.5 else want)
        last = cuts[-1]
    cuts.append(speech1)
    return list(zip(cuts, cuts[1:]))


def voice(lines, work):
    """reel.voice's contract (wav, timing, total): the whole script in ONE request, 0.25 s lead, -14 LUFS, 48 kHz.
    Waits for the picture search first: a story without enough pictures never spends one of the 10 daily F1 voices."""
    PICTURES_READY.wait(1800)
    words = sum(len(l.split()) for l in lines)
    need = max(10, int(words / 2.4 / reel.MAX_PIECE * 0.9))  # F1 MEASURED ~2.4 words/s (38 words = 15.6 s)
    if PICTURES['n'] is None or PICTURES['n'] < need:
        raise RuntimeError('only {} usable pictures for ~{} pieces: voice not requested'.format(PICTURES['n'], need))
    raw = os.path.join(work, 'voice_raw.wav')
    if not _f1('\n'.join(lines), raw):
        print('  F1 voice: both free models used up today -> Fish voice for this Reel', flush=True)
        _fish('\n'.join(lines), raw)
    timing = [(a + 0.25, b + 0.25) for a, b in line_timing(raw, lines)]
    wav = os.path.join(work, 'voice.wav')
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', raw, '-af', 'adelay=250|250,loudnorm=I=-14:TP=-1:LRA=11',
                    '-ar', '48000', '-ac', '1', wav], check=True)
    return wav, timing, _duration(raw) + 0.25 + 0.4


reel.voice = voice
_build = reel.build


def build(*a, **k):
    PICTURES['n'] = None
    PICTURES_READY.clear()  # each story waits for its OWN picture search
    return _build(*a, **k)


reel.build = build
reel.word_times = lambda *a, **k: None  # word timing exists only for captions


def no_text(headline, page, lines, timing, total, plan, facts, names, path, words=None):
    """User: no captions. An ASS file with no events (the renderer's subtitles step still runs, drawing nothing)."""
    open(path, 'w').write('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n'
                          'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, '
                          'Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
                          'Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Cap,Anton,80,&H00FFFFFF,&H00FFFFFF,'
                          '&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,2,60,60,480,1\n\n[Events]\n'
                          'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')


reel._overlay_ass = no_text
reel.overlay_ok = lambda *a, **k: None  # no fact boxes / name titles (and so none of their sound effects)
_edit = reel.edit_plan


def edit_plan(pieces, found, facts, names):
    """Every piece fills the phone screen (no band over a blurred copy)."""
    plan = _edit(pieces, found, facts, names)
    for p in plan or []:
        p['layout'] = 'full'
    return plan


reel.edit_plan = edit_plan


# ---- strict picture check -----------------------------------------------------------------------------------------------
def _langs():
    out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True).stdout.split()
    # MEASURED: adding guj made it WORSE (a Gujarati infographic fell from 15 words read to 1: the models compete)
    return '+'.join(l for l in ('eng', 'hin', 'pan') if l in out) or 'eng'


LANGS = _langs()


def _real_word(tok):
    """MEASURED 2026-10-24: Hindi/Punjabi OCR reads photo texture as Indian-script digits ("੨੩੩", "४१") and 2-letter Latin
    junk, which refused clean Fadnavis photos until stories ran out of pictures. A word = 3+ Latin letters, or 2+
    Devanagari/Gurmukhi letters (digits never count)."""
    latin = len(re.findall(r'[A-Za-z]', tok))
    indic = len(re.findall(r'[\u0900-\u0965\u0970-\u097F\u0A00-\u0A65\u0A70-\u0A7F]', tok))
    return latin >= 3 or indic >= 2


def _ocr(img):
    """Confident words of 2+ letters in any of the scripts, and how many are big (> 6 % of the height)."""
    from PIL import Image
    small = img
    try:  # MEASURED: a full-size photo took > 90 s in 3-language OCR and crashed a whole Reel; 1600 px is plenty for text
        im = Image.open(img)
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600))
            fd, small = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            im.convert('RGB').save(small)
    except OSError:
        pass
    try:
        r = subprocess.run(['tesseract', small, '-', '-l', LANGS, '--psm', '11', 'tsv'], capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        return 99, 9  # unreadable in time: refused (only this picture; a spare replaces it)
    finally:
        if small != img and os.path.exists(small):
            os.remove(small)
    words, big, img_h = 0, 0, 0
    for row in r.stdout.decode('utf-8', 'ignore').splitlines()[1:]:
        c = row.split('\t')
        if len(c) != 12:
            continue
        if c[0] == '1':
            img_h = int(c[9] or 0)
        try:
            conf, h = float(c[10]), int(c[9])
        except ValueError:
            continue
        if conf >= 60 and _real_word(c[11]):
            words += 1
            big += bool(img_h and h > 0.06 * img_h)
    return words, big


def text_heavy(path, max_words=6):
    """3 frames per clip (start, middle, end), OCR in English + Hindi + Punjabi, and a clip with a shot change inside is
    refused (a fast-cut news package drifts to other pictures: one went from border troops to Xi Jinping)."""
    frames = [path]
    if path.endswith('.mp4'):
        cuts = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-vf', "select='gt(scene,0.35)',showinfo", '-f', 'null', '-'],
                              capture_output=True, text=True).stderr.count('pts_time:')
        if cuts:
            print('  clip has {} shot change(s) inside, refused'.format(cuts), flush=True)
            return True
        d = _duration(path)
        frames = []
        for t in (0.3, d / 2, max(0.3, d - 0.4)):
            fd, f = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', '{:.2f}'.format(t), '-i', path, '-frames:v', '1', f])
            frames.append(f)
    try:
        counts = [_ocr(f) for f in frames]
        bad = any(w > max_words or b >= 2 for w, b in counts)
        if bad:  # kept for review (deleted with the work folder): refusals are measured, not guessed
            import shutil
            keep = os.path.join(os.path.dirname(path), 'refused')
            os.makedirs(keep, exist_ok=True)
            shutil.copy(path, os.path.join(keep, '{}-ocr{}.{}'.format(
                os.path.basename(path).rsplit('.', 1)[0], '-'.join('{}w{}b'.format(w, b_) for w, b_ in counts), path.rsplit('.', 1)[1])))
        return bad
    finally:
        for f in frames:
            if f != path and os.path.exists(f):
                os.remove(f)


moments.text_heavy = text_heavy

# ---- final look at every picture of the Reel together ---------------------------------------------------------------
FINAL = """You are the last check before a vertical Hindi news Reel about "{story}" is published.
The sheet shows its pieces in order, each numbered, EXACTLY as the phone screen will show them (already cropped);
a CLIP shows 3 frames (start, middle, end) side by side.
For each piece: the sentence spoken over it, and what the picture should show.
{pieces}

Mark a piece BAD when ANY of these is true:
- it is (or contains) a TV graphic, show title card, big headline banner, breaking-news screen, lower-third with long text,
  subtitles, a quote card, a logo slate, an advert, an app/download promo, or a video PLAY button;
- it is a split screen, a collage, a screenshot of a website or phone, or a thumbnail;
- it shows a person who is NOT one of this story's people ({names}) (look closely at faces: relatives and look-alikes
  count as wrong, e.g. a cousin of the named politician). A picture of ANY of the story's people is fine on any sentence:
  news B-roll shows the story's people while the sentence talks about another of them;
- it shows a place or event unrelated to the story;
- it is blank, black, blurry, or the clip's frames jump to unrelated pictures;
- the person or thing the piece should show is not in the frame (e.g. only flowers, a wall or a bystander are visible,
  because the subject moved out of the crop).
A broadcaster's small corner logo or microphones with channel logos are fine.
Return JSON: {{"bad": [{{"n": <piece number>, "why": "<= 10 words"}}]}} (an empty list when every piece is fine)."""


def _phone(im, cx):
    """The part of a picture the phone screen will show: the 9:16 crop around cx (as render_piece crops it)."""
    if im.width / im.height <= 9 / 16 + 0.01:
        return im
    cw = int(im.height * 9 / 16)
    c = 0.5 if cx is None else min(1.0, max(0.0, float(cx)))
    x = int(max(0, min(im.width - cw, im.width * c - cw / 2)))
    return im.crop((x, 0, x + cw, im.height))


def _tile(path, w=270, h=480, cx=None):
    """One piece as a 270x480 tile, AS THE PHONE WILL SHOW IT (cropped around cx): a photo whole, a clip as 3 frames
    side by side (2026-10-24: a moving speaker walked out of a fixed crop, leaving flowers and a pilot on screen)."""
    from PIL import Image
    tile = Image.new('RGB', (w, h), 'black')
    if path.endswith('.mp4'):
        d = _duration(path)
        for k, t in enumerate((0.3, d / 2, max(0.3, d - 0.4))):
            fd, f = tempfile.mkstemp(suffix='.jpg')
            os.close(fd)
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', '{:.2f}'.format(t), '-i', path, '-frames:v', '1', f])
            try:
                im = _phone(Image.open(f).convert('RGB'), cx)
                im.thumbnail((w // 3, h))
                tile.paste(im, (k * (w // 3) + (w // 3 - im.width) // 2, (h - im.height) // 2))
            except OSError:
                pass
            os.remove(f)
    else:
        im = _phone(Image.open(path).convert('RGB'), cx)
        im.thumbnail((w, h))
        tile.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return tile


def _sheet(files, path, cxs=None):
    from PIL import Image, ImageDraw
    cols = 6
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * 270, rows * 480), 'white')
    dr = ImageDraw.Draw(sheet)
    for i, f in enumerate(files):
        x, y = (i % cols) * 270, (i // cols) * 480
        sheet.paste(_tile(f, cx=(cxs or [None] * len(files))[i]), (x, y))
        dr.rectangle([x, y, x + 44, y + 30], fill='black')
        dr.text((x + 8, y + 8), str(i), fill='yellow')
    sheet.save(path, quality=85)
    return path


def verify(plan, files, spare, fetch, names=()):
    """Up to three looks: one vision request over a contact sheet of the whole Reel; every BAD piece is swapped for the
    next spare that passes the per-file checks and the swaps are looked at again, so a Reel is never packed with an
    unchecked replacement (MEASURED 2026-10-24: OCR cannot read a stylised "MAHARASHTRA FILES" title
    card, a video play button, or Uddhav Thackeray shown for Raj Thackeray)."""
    work = os.path.dirname(files[0])
    for rnd in range(3):
        lines = '\n'.join('{} | {} | said: "{}" | should show: {}'.format(
            n, p['moment']['kind'], p['text'][:90], p['moment'].get('desc', '')) for n, p in enumerate(plan))
        out = ai.gemini_vision(FINAL.format(story=' '.join(p['text'] for p in plan[:2])[:160], pieces=lines,
                                            names=', '.join(names) or 'the people the sentences name'),
                               [_sheet(files, os.path.join(work, 'final_{}.jpg'.format(rnd)), [p['moment'].get('cx') for p in plan])])
        bad = sorted({b.get('n') for b in (out.get('bad') or []) if isinstance(b, dict) and isinstance(b.get('n'), int)
                      and 0 <= b['n'] < len(plan)})
        for b in out.get('bad') or []:
            if isinstance(b, dict):
                print('  final check: piece {} bad: {}'.format(b.get('n'), str(b.get('why'))[:60]), flush=True)
        if not bad:
            return files
        if rnd == 2:  # still flagged after two rounds of replacements
            return None
        good_clips = [p['moment'] for i, p in enumerate(plan) if i not in bad and p['moment'].get('kind') == 'clip']
        taken = {(p['moment'].get('vid'), round(p['moment'].get('t', -1))) for p in plan}
        for n in bad:
            files[n] = None
            if not spare:  # no spares left: new moments from the clips that passed, 6-12 s away (different footage)
                for m in good_clips:
                    for dt in (6, -6, 12, -12):
                        t = m['t'] + dt
                        if t >= 0 and (m['vid'], round(t)) not in taken:
                            taken.add((m['vid'], round(t)))
                            spare.append(dict(m, t=t, desc=m.get('desc', '') + ' (nearby moment)'))
            while spare and not files[n]:
                cand = spare.pop(0)
                files[n] = fetch(n, cand)
                if files[n]:
                    plan[n]['moment'] = cand
            if not files[n]:
                return None
    return None


reel.VERIFY = verify
