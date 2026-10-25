"""PROFILE=hi: the Hindi Page (Indian Daily News). Same engine as the US Page; this module, imported by run.py before
anything runs, switches what differs. The US Page never imports it.
User decisions (2026-10-23/24): Hindi Reels only, 12 a day; Fish voice "शांत हिंदी पुरुष आवाज़" on the free model; most
viral Indian stories; Hindi + English searches; pictures fill the phone screen (vertical sources first, wide ones cropped
around the subject); no captions or other text on screen; strict picture checks (measured on the test Reels: one-frame
English-only OCR let an app advert, news banners, subtitles and a Punjabi banner through)."""
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

from . import ai, moments, net, reel, schedule, sources

FISH_VOICE = os.environ.get('HINDI_VOICE', '6a9e3e125639485bbe404363818ef997')  # fish-MA, chosen by the user
FISH_MODEL = 's2.1-pro-free'  # Fish's free model (same model as paid s2.1-pro; Channel Studio uses it)
FISH_KEY = os.path.expanduser(os.environ.get('FISH_KEY_FILE', '~/.fish_api_key'))


US_QUEUE = os.environ.get('US_REEL_QUEUE', '/home/poster/state/reel_queue')
US_TZ, US_WINDOW = 'America/New_York', (360, 1380)  # the US Page's posting hours (its schedule defaults)


def english_needs_build():
    """User 2026-10-24: English must not be affected at all. Both Pages share one build lock (the VM's 1 GB); a Hindi build
    started while the US Page has NO Reel ready could make an English slot pass empty -> Hindi waits until English has one."""
    from zoneinfo import ZoneInfo
    if not schedule.build_time(ZoneInfo(US_TZ), US_WINDOW):
        return False  # US night: the US builder is idle, Hindi has the server to itself
    try:
        with open(os.path.join(os.path.dirname(US_QUEUE), 'reel_ai_pause.json')) as f:
            if json.load(f).get('until', '') > schedule.now().isoformat():
                return False  # the US builder's AI is down for now: it cannot build anyway, Hindi uses the turn
    except (OSError, ValueError):
        pass
    try:
        return os.path.isdir(US_QUEUE) and not any(os.path.exists(os.path.join(US_QUEUE, d, 'meta.json')) for d in os.listdir(US_QUEUE))
    except OSError:
        return False


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
# RESEARCHED 2026-10-24: ANI struck YouTubers for 9-11 s clips and demanded Rs 15-48 lakh licences (BOOM, SpicyIP);
# Prasar Bharati/DD strikes clips of Parliament and PM speeches (Scroll, MediaNama) -> their footage is never cut
moments.STRICT = re.compile(moments.STRICT.pattern[:-3] + r'|bcci|ipl|icc|star sports|jiocinema|jiohotstar|hotstar|sony ?liv|'
                            r'sony sports|t-series|zee music|tips official|yrf|yash raj|dharma|saregama|eros|'
                            r'ani|ani news|asian news international|prasar bharati|doordarshan|dd news|dd india|sansad tv|'
                            r'lok sabha tv|rajya sabha tv)\b', re.I)
moments.WIDE_ONLY, moments.PREFER_VERTICAL = False, True
# MEASURED 2026-10-24 (Daler Mehndi story): 18 usable moments for 20 pieces -> more clips per story, and pieces up to
# 3.5 s (user: B-roll under 3-4 s) so a 50 s Reel needs ~15 pieces instead of ~19
moments.MAX_VIDEOS, moments.PER_VIDEO = 7, 3
# 2026-10-24 live: re-encoding 1080p cuts (--force-keyframes-at-cuts) on the e2-micro timed out at 300 s, 3 at a time,
# and loaded the VM the US Page shares -> cut on existing keyframes (stream copy: no encoding; a clip may start a
# couple of seconds early, and the 3-frame checks + final look judge what is really in it)
moments.CUT_EXTRA = []
reel.MAX_PIECE = 5.0  # user 2026-10-24 "longer photo shots": ~20% fewer pictures a Reel (4.0 left 16 of 22 builds short)
moments.CHECK = moments.CHECK.replace('the picture editor of a US news page', 'the picture editor of an Indian news page').replace(
    # MEASURED 2026-10-25: ~34 of 42 refused downloads were Indian TV frames (NewsX, NDTV, India Speed News) whose chyron WAS
    # about the story, so this US rule kept them; each then cost a download + ~1.5 min of OCR before being refused
    "A news channel's own logo or a chyron ABOUT THIS STORY is fine.",
    'A small channel logo in a corner is fine. ANY other TV text is NOT: reject a frame with a chyron, ticker, headline '
    'box, lower third, breaking-news banner, or channel graphics framing the picture, even when it is about this story.').replace(
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


VISUAL = {'ENTERTAINMENT', 'CRICKET', 'SPORTS', 'WEATHER', 'SCIENCE', 'TECH'} | set(moments.SPORTS_TAGS)


def pick_stories(cands, recent):
    """User 2026-10-24 "prefer visual stories": MEASURED, Indian politics mostly fails on pictures (TV-graphic packages),
    so stories with clean real footage go first; the viral order is kept inside each group; politics still runs."""
    picks = sorted(_pick(cands, recent), key=lambda p: (p.get('tag') or '').upper() not in VISUAL)
    for p in picks:
        if isinstance(p.get('query_hi'), str) and p['query_hi'].strip():
            HI_QUERY[p['query']] = p['query_hi'].strip()
    return picks


def yt_search(query, subject_words, n=10):
    out = _yt_search(query, subject_words, n)
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


# official channels whose material may be reused (PIB: "may be reproduced free of charge, no prior approval", source
# acknowledged) are tried first. NOT ANI / DD / Sansad TV: they strike short clips (see STRICT)
AGENCY = re.compile(r'(?i)\b(pib|pib india|pmo|narendra modi|mea ?india)\b')
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

    def need_for(ls):  # F1 MEASURED ~2.4 words/s (38 words = 15.6 s); pieces end on sentence breaks, so they average
        # 0.8 x MAX_PIECE (MEASURED 2026-10-24: 51 s -> 13, 54 -> 13, 58 -> 15, 59 -> 14 pieces; this gives 13/14/15/15)
        return max(10, math.ceil(sum(len(l.split()) for l in ls) / 2.4 / (reel.MAX_PIECE * 0.8)))
    found, before = PICTURES['n'] or 0, len(lines)
    # user: a Reel may be 45 s to 2 min -> fewer pictures make a SHORTER Reel, not none (live 2026-10-24: 11 pictures for
    # ~15 pieces was skipped). Detail lines go from the end; the hook (first) and the question (last) stay; >= 110 words
    # (~45 s). In place: build uses this same list for the pieces and the screen text.
    while found < need_for(lines) and len(lines) > 9 and sum(len(l.split()) for l in lines[:-2] + lines[-1:]) >= 110:
        lines[:] = lines[:-2] + lines[-1:]
    if len(lines) < before:
        print('  script shortened to {} lines for {} pictures'.format(len(lines), found), flush=True)
    need = need_for(lines)
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


CURRENT = {}


def build(page, outlets, story, query, subject, tag, work, used=()):
    PICTURES['n'] = None
    PICTURES_READY.clear()  # each story waits for its OWN picture search
    CURRENT.update(story=story, query=query, work=work, used=set(used), replenished=False, fetch_replenished=False, hashes=[])
    return _build(page, outlets, story, query, subject, tag, work, used)


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


reel.SUBS_FILTER = 'ass={ass}:fontsdir={fonts}:shaping=complex'

# ---- on-screen key phrases (user 2026-10-24: "see how DW Hindi adds text on Reels ... invent better text placing in that
# style"). MEASURED on 3 DW Hindi Reels: short 2-line key phrases (not subtitles), each line in its own tight box, left
# lower-middle, key words in a 2nd colour; name in white + role in a small box; a big number with an underline. Ours:
# deep red boxes, saffron-yellow key words, Noto Sans Devanagari ExtraBold (OFL) ---------------------------------------
SCREEN = """You design the ON-SCREEN TEXT for a vertical Hindi news Reel (short key phrases like the best news pages, never
subtitles). The narration lines are below with their numbers. Return JSON:
{{"phrases": [{{"line": <line number>, "text": "<key phrase in Hindi Devanagari, 3-8 words, the punch of that moment; wrap 1-2
  words to highlight in *asterisks*>"}}],
  "names": [{{"line": <line where the person is first mentioned>, "name": "<name in Hindi>", "name_en": "<the same name in English>",
  "role": "<role in Hindi, <= 5 words>"}}],
  "number": {{"line": <line with the most striking number, or -1>, "text": "<that number in digits + unit in Hindi, <= 3 words>"}}}}
Rules: a phrase on about every second line (never two lines in a row); line 0 ALWAYS gets the hook phrase; only facts from
the lines; no hashtags, no emojis.
LINES:
{lines}"""
RED, YELLOW = '&H002E10C8', '&H0000D4FF'  # ASS colours are BGR: #C8102E deep red, #FFD400 saffron-yellow
FONT_HI = 'Noto Sans Devanagari ExtraBold'


def _wrap(text, width=15):
    """at most 2 lines, split at the space nearest the middle when longer than `width` letters"""
    if len(text.replace('*', '')) <= width or ' ' not in text:
        return [text]
    mid, spaces = len(text) // 2, [i for i, c in enumerate(text) if c == ' ']
    cut = min(spaces, key=lambda i: abs(i - mid))
    return [text[:cut].strip(), text[cut:].strip()]


def _hl(text):
    """*word* -> that word in saffron-yellow (ASS colour tags); braces in the text can never break the line"""
    text = text.replace('{', '(').replace('}', ')')
    return re.sub(r'\*([^*]+)\*', lambda m: '{\\c' + YELLOW + '&}' + m.group(1) + '{\\c&HFFFFFF&}', text)


def screen_text(lines, timing, plan, path):
    """Writes the Reel's ASS: key phrases, name tags (only over a picture that shows that person), one big number."""
    try:
        spec = ai.gemini(SCREEN.format(lines='\n'.join('{} | {}'.format(i, l) for i, l in enumerate(lines))), chain='best')
    except Exception as e:  # noqa: BLE001 - optional: the Reel is fine without text
        print('  screen text skipped: {}: {}'.format(type(e).__name__, str(e)[:100]), flush=True)
        spec = {}
    if not isinstance(spec, dict):
        spec = {}
    T = reel._ass_time
    ev = []
    for ph in spec.get('phrases') or []:
        i = ph.get('line') if isinstance(ph, dict) else None
        if not isinstance(i, int) or not 0 <= i < len(timing) or not ph.get('text'):
            continue
        a, b = timing[i][0] + 0.15, timing[i][1] - 0.1
        rows = _wrap(ph['text'])
        for k, row in enumerate(rows):
            y = 1290 - (len(rows) - 1 - k) * 128  # MEASURED DW: text ~5.5 % of the height (ours was 3.3 %)
            ev.append('Dialogue: 3,{},{},Key,,0,0,0,,{{\\move(30,{y},72,{y},0,160)\\fad(90,120)}}{t}'.format(
                T(a + 0.06 * k), T(b), y=y, t=_hl(row)))
    name_on = None

    def piece_at(t):
        return next((p for p in plan if p['a'] <= t < p['b']), None)
    for nm in spec.get('names') or []:
        i = nm.get('line') if isinstance(nm, dict) else None
        if not isinstance(i, int) or not 0 <= i < len(timing) or not nm.get('name'):
            continue
        a = timing[i][0] + 0.3
        p = piece_at(a)
        words = [w for w in re.findall(r'[a-z]+', (nm.get('name_en') or '').lower()) if len(w) >= 3]
        desc = (p or {}).get('moment', {}).get('desc', '').lower()
        if not (p and p['moment'].get('kind') == 'photo' and words and any(w in desc for w in words)):
            continue  # never a name over someone else's picture (the US Page once put "BEARS" over a Patriots player)
        b = min(timing[i][1], a + 2.8)
        name_on = (a, b)
        ev.append('Dialogue: 4,{},{},NameT,,0,0,0,,{{\\pos(72,900)\\fad(100,120)}}{}'.format(T(a), T(b), _hl(nm['name'])))
        if nm.get('role'):
            ev.append('Dialogue: 4,{},{},Role,,0,0,0,,{{\\pos(78,975)\\fad(100,120)}}{}'.format(T(a + 0.1), T(b), _hl(nm['role'])))
        break  # one name tag per Reel at most (DW: on the person when they first appear)
    num = spec.get('number') if isinstance(spec.get('number'), dict) else {}
    i = num.get('line')
    if isinstance(i, int) and 0 <= i < len(timing) and num.get('text'):
        a = timing[i][0] + 0.2
        if name_on and a < name_on[1] and name_on[0] < a + 2.4:  # live 2026-10-24: "11 साल" landed on the name tag
            a = name_on[1] + 0.15
        b = min(timing[i][1], a + 2.4)
    else:
        a = b = 0
    if b - a >= 1.2:  # a number squeezed under 1.2 s by the name tag is left out
        ev.append('Dialogue: 5,{},{},Num,,0,0,0,,{{\\pos(540,860)\\fad(80,140)\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)}}{}'.format(
            T(a), T(b), num['text'].replace('{', '(').replace('}', ')')))
        ev.append('Dialogue: 5,{},{},Bar,,0,0,0,,{{\\pos(540,965)\\fad(80,140)\\p1}}m -330 0 l 330 0 330 18 -330 18{{\\p0}}'.format(T(a + 0.15), T(b)))
    head = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, '
            'StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Key,{f},92,&H00FFFFFF,&H00FFFFFF,{r},{r},0,0,0,0,100,100,0,0,3,18,0,1,60,60,60,1\n'
            'Style: NameT,{f},84,&H00FFFFFF,&H00FFFFFF,&H00141414,&H00000000,0,0,0,0,100,100,0,0,1,5,2,1,60,60,60,1\n'
            'Style: Role,{f},56,&H00FFFFFF,&H00FFFFFF,{r},{r},0,0,0,0,100,100,0,0,3,10,0,1,60,60,60,1\n'
            'Style: Num,{f},190,&H00FFFFFF,&H00FFFFFF,&H00141414,&H00000000,0,0,0,0,100,100,0,0,1,7,3,5,60,60,60,1\n'
            'Style: Bar,Arial,20,{y},{y},{y},{y},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n').format(f=FONT_HI, r=RED, y=YELLOW)
    open(path, 'w').write(head + '\n'.join(ev) + '\n')
    print('  screen text: {} phrases, {} name tags, {} number'.format(
        sum(1 for e in ev if ',Key,' in e), sum(1 for e in ev if ',NameT,' in e), sum(1 for e in ev if ',Num,' in e)), flush=True)
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
    try:
        out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True).stdout.split()
    except OSError:  # no OCR installed on this machine
        return 'eng'
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
        if max(im.size) > 1280:  # MEASURED: 1280 vs 1600 px gives the same verdict on 37 of 42 refusals, 75 % of the time
            im.thumbnail((1280, 1280))
            fd, small = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            im.convert('RGB').save(small)
    except OSError:
        pass
    try:  # MEASURED on the VM: 1 thread 32 s vs 79 s default (threads fight over the e2-micro's shared CPU)
        r = subprocess.run(['tesseract', small, '-', '-l', LANGS, '--psm', '11', 'tsv'], capture_output=True, timeout=60,
                           env=dict(os.environ, OMP_THREAD_LIMIT='1'))
    except subprocess.TimeoutExpired:
        return None  # unread in time: not refused here, the final look at the whole Reel judges it (MEASURED 2026-10-24:
        # 4 of 5 photos refused for a timeout were clean: planes, MPs protesting)
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


def _dhash(img):
    from PIL import Image
    g = Image.open(img).convert('L').resize((9, 8))
    px = list(g.getdata())
    return sum(1 << i for i in range(64) if px[i // 8 * 9 + i % 8] > px[i // 8 * 9 + i % 8 + 1])


def _seen_before(img):
    """Live 2026-10-24 (chess Reel): one photo from two web addresses was shown twice; media keys compare addresses only.
    A difference hash survives resizing and recompression; 10 of 64 bits apart or less = the same picture."""
    try:
        h = _dhash(img)
    except Exception:  # noqa: BLE001 - unreadable frame: the other checks decide
        return False
    seen = CURRENT.setdefault('hashes', [])
    if any(bin(h ^ x).count('1') <= 10 for x in seen):
        return True
    seen.append(h)
    return False


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
        for t in (0.3, max(0.3, d - 0.4)):  # one shot (cut check above): start + end caught 23 of 25 refusals of 3 frames
            fd, f = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', '{:.2f}'.format(t), '-i', path, '-frames:v', '1', f])
            frames.append(f)
    try:
        if _seen_before(frames[len(frames) // 2]):
            print('  same picture as one already in this Reel, refused', flush=True)
            return True
        counts = [c for c in (_ocr(f) for f in frames) if c]
        # user 2026-10-24 "allow text inside the scene": big words count from 4 (MEASURED on 42 refusals: >= 2 refused an FBI
        # vest, a "Gate 7" sign and a name plate; now all 7 clean ones pass, 26 of 35 TV graphics are still refused here and
        # the other 9 (8 OCR timeouts, 1 anchor shot) go to the final look)
        bad = any(w > max_words or b >= 4 for w, b in counts)
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

_cut = moments.cut
CUT_MARGIN = 2.5  # seconds downloaded on each side of a moment, so a camera cut inside it can be stepped around


def _shot_cuts(path):
    err = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-vf', "select='gt(scene,0.35)',showinfo", '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    return [float(x) for x in re.findall(r'pts_time:([\d.]+)', err)]


def cut(vid, t, dur, out):
    """MEASURED 2026-10-24 (5 s shots): 11 of 13 refusals in one build were clips with a camera cut inside (news packages
    cut every 1-3 s). A wider window is downloaded and the stretch without a cut nearest the moment is kept; a clip with
    no long enough stretch is kept whole and refused by the same check as before."""
    a0 = max(0.0, t - CUT_MARGIN)
    wide = out[:-4] + '-wide.mp4'
    if not _cut(vid, a0, t - a0 + dur + CUT_MARGIN, wide):
        return None
    try:
        bounds = [0.0] + _shot_cuts(wide) + [_duration(wide)]
        want, best = t - a0, None
        for a, b in zip(bounds, bounds[1:]):
            a, b = a + 0.15, b - 0.1  # clear of the frames at the cut
            if b - a >= dur:
                s_ = min(max(want, a), b - dur)
                if best is None or abs(s_ - want) < abs(best - want):
                    best = s_
        if best is None:
            os.replace(wide, out)
            return out
        r = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y', '-ss', '{:.2f}'.format(best), '-i', wide,
                            '-t', '{:.2f}'.format(dur), '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-c:a', 'aac', out])
        return out if r.returncode == 0 and os.path.exists(out) else None
    finally:
        if os.path.exists(wide):
            os.remove(wide)


moments.cut = cut

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
Do NOT mark a piece bad only because it shows another moment of the story than its sentence describes: an old photo,
another event or place of the same people, a portrait or statue of them is normal news B-roll and is fine.
A broadcaster's small corner logo or microphones with channel logos are fine. Text that is physically part of the real
scene is fine too: protest placards and banners held by people, shop and street signs, name plates, words on clothing.
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


def _replenish(plan, spare, names):
    """Once per Reel: a second picture search (the Hindi query, then each person's name alone) so a story with a few
    rejected pictures is finished instead of dropped. Same checks as the first search."""
    CURRENT['replenished'] = True
    used = set(CURRENT.get('used') or ()) | {reel.media_key(p['moment']) for p in plan} | {reel.media_key(m) for m in spare}
    queries = [q for q in [HI_QUERY.get(CURRENT.get('query'))] + list(names)[:3] if q]
    for i, q in enumerate(queries[:3]):
        try:
            more = _find(CURRENT.get('story', ''), list(names), q, os.path.join(CURRENT['work'], 'more{}'.format(i)), want=24,
                         clips_ok=True, used=used)
        except Exception as e:  # noqa: BLE001 - the Reel then works with the spares it has
            print('  extra picture search failed: {}: {}'.format(type(e).__name__, str(e)[:100]), flush=True)
            continue
        new = [m for m in more if reel.media_key(m) not in used]
        used |= {reel.media_key(m) for m in new}
        spare.extend(new)
        print('  extra picture search "{}": {} new moments'.format(q[:40], len(new)), flush=True)
        if len(spare) >= 8:
            break


def replenish_fetch(plan, spare, names):
    """The downloads ran out of spares (live 2026-10-24: Tilak Varma, 18 moments for 15 pieces): one extra picture search
    for this stage, separate from the one the final look may still need."""
    if CURRENT.get('fetch_replenished'):
        return
    CURRENT['fetch_replenished'] = True
    keep = CURRENT.get('replenished')
    _replenish(plan, spare, names)
    CURRENT['replenished'] = keep


reel.REPLENISH = replenish_fetch
reel.LOCAL_RENDER = False  # live 2026-10-25: the fallback render (ffmpeg 789 MB) ran the VM out of memory and offline for 80 min


def photo(url, out):
    """moments.photo with the reason printed when a photo is dropped (it was silent: a story failed with no clue why)."""
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': moments.BING_HEADERS['User-Agent']}),
                                     timeout=25).read()
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        if im.width < 700:
            print('  photo dropped (only {} px wide): {}'.format(im.width, url[:80]), flush=True)
            return None
        im.save(out, quality=92)
        return out
    except Exception as e:  # noqa: BLE001 - one photo; a spare replaces it
        print('  photo dropped ({}: {}): {}'.format(type(e).__name__, str(e)[:60], url[:80]), flush=True)
        return None


moments.photo = photo


MERGE_MAX = 9.0  # seconds a photo may stay on screen when it takes over a dropped piece's time


def _merge_out(plan, files, bad):
    """Live 2026-10-24/25: 12 stories in 27 h (325 min of building) died at the final check with a few pieces still bad.
    Up to 3 bad pieces leave the Reel and the PHOTOS next to them stay on screen longer (a clip is cut to its exact length,
    so it never stretches). Only checked pictures remain, none repeated. False = cannot (plan and files untouched)."""
    if not bad or len(bad) > 3 or len(plan) - len(bad) < 8:
        return False
    kept = [i for i in range(len(plan)) if i not in bad]
    new = [dict(plan[i]) for i in kept]
    for k in range(len(new) + 1):  # the gap before new[k] (k == len: after the last)
        a = new[k - 1]['b'] if k else plan[0]['a']
        b = new[k]['a'] if k < len(new) else plan[-1]['b']
        if b - a < 0.01:
            continue
        prev = new[k - 1] if k and new[k - 1]['moment'].get('kind') == 'photo' else None
        nxt = new[k] if k < len(new) and new[k]['moment'].get('kind') == 'photo' else None
        if prev and nxt:
            prev['b'] = nxt['a'] = (a + b) / 2
        elif prev:
            prev['b'] = b
        elif nxt:
            nxt['a'] = a
        else:
            return False  # only clips around the gap
    if any(p['b'] - p['a'] > MERGE_MAX for p in new):
        return False
    print('  final check: {} piece(s) dropped, neighbouring photos hold longer'.format(len(bad)), flush=True)
    plan[:] = new
    files[:] = [files[i] for i in kept]
    return True


def verify(plan, files, spare, fetch, names=()):
    """Up to three looks: one vision request over a contact sheet of the whole Reel; every BAD piece is swapped for the
    next spare that passes the per-file checks and the swaps are looked at again, so a Reel is never packed with an
    unchecked replacement (MEASURED 2026-10-24: OCR cannot read a stylised "MAHARASHTRA FILES" title
    card, a video play button, or Uddhav Thackeray shown for Raj Thackeray)."""
    work = os.path.dirname(files[0])
    for rnd in range(3):
        lines = '\n'.join('{} | {} | said: "{}" | should show: {}'.format(
            n, p['moment']['kind'], p['text'][:90], p['moment'].get('desc', '')) for n, p in enumerate(plan))
        sheet = _sheet(files, os.path.join(work, 'final_{}.jpg'.format(rnd)), [p['moment'].get('cx') for p in plan])
        out = None
        for _ in range(2):  # MEASURED 2026-10-24: the model once answered null ('NoneType' has no attribute 'get')
            out = ai.gemini_vision(FINAL.format(story=' '.join(p['text'] for p in plan[:2])[:160], pieces=lines,
                                                names=', '.join(names) or 'the people the sentences name'), [sheet])
            if isinstance(out, dict):
                break
        if not isinstance(out, dict):
            return None  # never packed unchecked
        bad = sorted({b.get('n') for b in (out.get('bad') or []) if isinstance(b, dict) and isinstance(b.get('n'), int)
                      and 0 <= b['n'] < len(plan)})
        for b in out.get('bad') or []:
            if isinstance(b, dict):
                print('  final check: piece {} bad: {}'.format(b.get('n'), str(b.get('why'))[:60]), flush=True)
        if not bad:
            return files
        if rnd == 2:  # still flagged after two rounds of replacements: dropped if the photos around them can hold
            return files if _merge_out(plan, files, bad) else None
        good_clips = [p['moment'] for i, p in enumerate(plan) if i not in bad and p['moment'].get('kind') == 'clip']
        taken = {(p['moment'].get('vid'), round(p['moment'].get('t', -1))) for p in plan}
        if len(spare) < len(bad) and not CURRENT.get('replenished'):
            _replenish(plan, spare, names)  # 7 of 16 failed Hindi stories on day 1 ran out of spares HERE
        drop = set()
        for n in bad:
            files[n] = None
            if not spare and not CURRENT.get('replenished'):  # spares ran out while swapping (downloads/text checks fail too)
                _replenish(plan, spare, names)
            if not spare:  # still none: new moments from the clips that passed, 6-12 s away (different footage)
                for m in good_clips:
                    for dt in (6, -6, 12, -12):
                        t = m['t'] + dt
                        if t >= 0 and (m['vid'], round(t)) not in taken:
                            taken.add((m['vid'], round(t)))
                            spare.append(dict(m, t=t, desc=m.get('desc', '') + ' (nearby moment)'))
            while not files[n]:
                if not spare and not CURRENT.get('replenished'):
                    _replenish(plan, spare, names)
                if not spare:
                    break
                cand = spare.pop(0)
                files[n] = fetch(n, cand)
                if files[n]:
                    plan[n]['moment'] = cand
            if not files[n]:
                drop.add(n)  # no replacement left for this piece
        if drop and not _merge_out(plan, files, drop):
            return None
    return None


reel.VERIFY = verify

# ---- opening clip WITH its own sound (user 2026-10-24: "in starting show original clips for 3 to 4 seconds with effects,
# keeping the clip's voice, so it increases curiosity") ------------------------------------------------------------------
HOOK_SECONDS = 3.5
# user 2026-10-24 (after the copyright research): hook WITHOUT the clip's sound for now (audio matching is the main claim
# trigger; ANI struck 9-11 s clips) - a soft whoosh into the flash instead. True = the clip's own voice (e.g. PB-SHABD clips)
HOOK_SOUND = False
SPEAKING = re.compile(r'(?i)speak|says|said|press|address|interview|statement|podium|rally|speech|briefing|media|talk|बोल|बयान')


def _loudness(path):
    """mean volume of the clip's sound in dB (None when it has no sound)"""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-vn', '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True)
    m = re.search(r'mean_volume: (-?[\d.]+) dB', r.stderr)
    return float(m.group(1)) if m else None


def hook(plan, spare, fetch, work):
    """A spare clip of the story's people SPEAKING (statement / press / interview first), cut with its own sound, through
    the same text and shot-change checks, with real sound in it. None: the Reel starts with the narration as before."""
    clips = [m for m in spare if m.get('kind') == 'clip']
    clips.sort(key=lambda m: not SPEAKING.search(m.get('desc', '')))
    used = {(p['moment'].get('vid'), round(p['moment'].get('t', -1))) for p in plan}
    for p in plan:  # nearby moments of the Reel's own clips as a last resort (different seconds of the same footage)
        m = p['moment']
        if m.get('kind') == 'clip' and (m['vid'], round(m['t'] - 8)) not in used and m['t'] >= 8:
            clips.append(dict(m, t=m['t'] - 8))
    for m in clips[:6]:
        out = os.path.join(work, 'hook_src.mp4')
        try:
            f = moments.cut(m['vid'], m['t'], HOOK_SECONDS + 0.4, out)
        except Exception:  # noqa: BLE001 - one candidate's trouble; the next is tried
            f = None
        if not f:
            continue
        if text_heavy(f):
            os.path.exists(f) and os.remove(f)
            continue
        if HOOK_SOUND:
            vol = _loudness(f)
            if vol is None or vol < -38:  # no sound or near silence: no point keeping "the clip's voice"
                print('  opening clip skipped: sound {} dB'.format(vol), flush=True)
                os.remove(f)
                continue
        if m in spare:
            spare.remove(m)
        print('  opening clip: {}'.format(m.get('desc', '')[:60]), flush=True)
        return {'src': f, 'dur': HOOK_SECONDS, 'cx': m.get('cx'), 'sound': HOOK_SOUND}
    return None


reel.HOOK = hook


def overlay_hi(headline, page, lines, timing, total, plan, facts, names, path, words=None):
    """Hindi on-screen text: key phrases / one name tag / one big number (screen_text), never word-by-word captions."""
    screen_text(lines, timing, plan, path)


reel._overlay_ass = overlay_hi
