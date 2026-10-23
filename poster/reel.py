"""45-60 s vertical news Reels (1080x1920): AI script -> Kokoro voice line by line -> real moments (moments.py)
-> an edit brain picks per 3 s piece the moment that matches the words, layout, motion, join and overlay -> ffmpeg.
Voice only, no music; the clips' own sound is never used."""
import html
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import ai, cloud
from .render import FONTS

VOICE = os.environ.get('REEL_VOICE', 'af_heart')
MODELS = os.path.expanduser(os.environ.get('KOKORO_DIR', '~/models'))
FPS = 30
GAP = 0.22  # pause between spoken lines, seconds

SCRIPT = """You write the voice-over for a 45-60 second vertical news Reel for a US Facebook news page (the page name is not part of the story; never mention it).
The story is whatever the SOURCES below report.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names, scenes or outcomes.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

Return JSON: {{"headline": str, "lines": [str, ...], "screen_lines": [str, ...], "facts": [str, str], "names": [str], "caption": str}}
- lines: 9 to 13 spoken sentences, 120-150 words in total, each at most 16 words. Written for the ear: short, clear,
  contractions, plain everyday words, no jargon, no abbreviations a narrator would stumble on (write "percent", "million").
  The lines are the VOICE: spell numbers out for the ear there.
  Line 1 (max 10 words) is the hook: the most surprising or emotional REAL detail, so a scroller stops in 2 seconds.
  Then what happened, why it matters to an ordinary American, and the human stakes.
  The LAST line is one specific question people will disagree about (never "comment below", "like if", "share").
- headline: max 7 words, punchy, factual, shown at the top of the screen the whole time. Use DIGITS and symbols here ($25 billion, 97-71), never spelled-out numbers.
- facts: two striking concrete details from the sources, max 9 words each, digits as in the headline.
- names: the people, places and organisations the story is about, exactly as the sources write them (max 4).
- screen_lines: the SAME lines, same count and order, but written for the screen: numbers as digits ($25 billion, 21 points, 97-71).
- caption: the Reel's description, 15-40 words, emotional but factual, no hashtags, no credits, no links.
No em dashes. Never use: delve, tapestry, testament, game-changer, buckle up, let that sink in, breaking news.

SOURCES:
{sources}"""




def script_problems(s):
    lines = s.get('lines') or []
    words = sum(len(l.split()) for l in lines)
    p = []
    if not 9 <= len(lines) <= 14:
        p.append('{} lines, need 9-13'.format(len(lines)))
    if not 110 <= words <= 165:
        p.append('{} words, need 120-150'.format(words))
    if lines and not lines[-1].rstrip().endswith('?'):
        p.append('last line must be a question')
    if re.search(r'\b(comment (yes|below)|like if|share (this|if)|tag a friend)\b', ' '.join(lines), re.I):
        p.append('engagement bait')
    return p


def voice(lines, work):
    """One Kokoro take per line so each line's exact start and end are known (captions and scene cuts use them)."""
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(os.path.join(MODELS, 'kokoro-v1.0.int8.onnx'), os.path.join(MODELS, 'voices-v1.0.bin'))
    parts, timing, t, sr = [], [], 0.25, 24000
    parts.append(np.zeros(int(0.25 * sr), dtype='float32'))
    for line in lines:
        samples, sr = k.create(line, voice=VOICE, speed=1.0, lang='en-us')
        d = len(samples) / sr
        timing.append((t, t + d))
        parts += [samples.astype('float32'), np.zeros(int(GAP * sr), dtype='float32')]
        t += d + GAP
    raw = os.path.join(work, 'voice_raw.wav')
    sf.write(raw, np.concatenate(parts), sr)
    wav = os.path.join(work, 'voice.wav')
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', raw, '-af', 'loudnorm=I=-14:TP=-1:LRA=11', '-ar', '48000', wav], check=True)
    return wav, timing, t + 0.4


def _ass_time(t):
    return '{:d}:{:02d}:{:05.2f}'.format(int(t // 3600), int(t % 3600 // 60), t % 60)


def word_times(wav, lines, timing):
    """Exact start/end of every word: Groq Whisper (free) word timestamps on our own voice track, mapped onto each
    sentence's words in order. Falls back to splitting each sentence by letters if Whisper is unavailable."""
    heard = []
    key = os.environ.get('GROQ_API_KEY')
    if key:
        import urllib.request
        import uuid
        b = uuid.uuid4().hex
        mp3 = wav[:-4] + '_asr.mp3'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', wav, '-ac', '1', '-ar', '16000', '-b:a', '48k', mp3], check=True)
        parts = []
        for k, v in (('model', 'whisper-large-v3-turbo'), ('response_format', 'verbose_json'), ('timestamp_granularities[]', 'word'),
                     ('language', 'en')):
            parts.append('--{}\r\nContent-Disposition: form-data; name="{}"\r\n\r\n{}\r\n'.format(b, k, v).encode())
        parts.append('--{}\r\nContent-Disposition: form-data; name="file"; filename="voice.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.format(b).encode()
                     + open(mp3, 'rb').read() + b'\r\n')
        try:
            req = urllib.request.Request('https://api.groq.com/openai/v1/audio/transcriptions',
                                         data=b''.join(parts) + '--{}--\r\n'.format(b).encode(),
                                         headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'news-poster',
                                                  'Content-Type': 'multipart/form-data; boundary=' + b})
            with urllib.request.urlopen(req, timeout=120) as r:
                heard = [(w['start'], w['end']) for w in json.loads(r.read()).get('words', [])]
        except Exception as e:  # noqa: BLE001 - timing falls back to the letter split
            print('  whisper timing unavailable ({}), using letter split'.format(type(e).__name__), flush=True)
    out = []
    for line, (a, b_) in zip(lines, timing):
        ws = line.split()
        inside = [h for h in heard if h[0] >= a - 0.15 and h[1] <= b_ + 0.15]
        if inside:
            # same count or not, each screen word takes the heard word at its relative position
            spans = [inside[min(len(inside) - 1, int(i * len(inside) / len(ws)))] for i in range(len(ws))]
            spans = [(max(a, s_), min(b_, e_)) for s_, e_ in spans]
        else:
            n = sum(len(w) + 1 for w in ws) or 1
            t, spans = a, []
            for w in ws:
                d = (b_ - a) * (len(w) + 1) / n
                spans.append((t, t + d))
                t += d
        out.append(list(zip(ws, spans)))
    return out


def captions(lines, timing, path, words=None):
    """Word-by-word highlighted captions (the most used short-form style): 2-3 words on screen, the spoken word in
    yellow and a touch bigger, white bold Montserrat Black with a black outline, in the lower third above Facebook's
    buttons. A group pops in when it starts."""
    if not words:
        words = [list(zip(l.split(), _even_split(l, t))) for l, t in zip(lines, timing)]
    ev = []
    for line_words in words:
        groups, cur = [], []
        for w, sp in line_words:
            cur.append((w, sp))
            if len(cur) >= 3 or (len(cur) >= 2 and w[-1] in ',.?!:;') or sum(len(x) + 1 for x, _ in cur) > 15:  # one line, no wrap
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        for g in groups:
            g_start, g_end = g[0][1][0], g[-1][1][1]
            for k, (w, (s_, e_)) in enumerate(g):
                start = g_start if k == 0 else s_
                end = g_end if k == len(g) - 1 else g[k + 1][1][0]
                text = ' '.join(('{\\1c&H00F2FF&\\fscx112\\fscy112}' + _cap(x) + '{\\1c&HFFFFFF&\\fscx100\\fscy100}') if j == k else _cap(x)
                                for j, (x, _) in enumerate(g))
                pop = '{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}' if k == 0 else ''
                ev.append('Dialogue: 0,{},{},Cap,,0,0,0,,{}{}'.format(_ass_time(start), _ass_time(max(end, start + 0.05)), pop, text))
    open(path, 'w').write("""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Montserrat Black,118,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,8,4,2,60,60,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + '\n'.join(ev) + '\n')


def _cap(word):
    return word.upper().replace('{', '(').replace('}', ')')  # a brace would start an ASS override block


def _even_split(line, span):
    a, b_ = span
    ws = line.split()
    n = sum(len(w) + 1 for w in ws) or 1
    t, out = a, []
    for w in ws:
        d = (b_ - a) * (len(w) + 1) / n
        out.append((t, t + d))
        t += d
    return out


MEDIA_TOP, MEDIA_H = 600, 760  # band layout: the picture band; the headline sits above it, the captions below
MAX_PIECE = 3.0  # seconds: every clip/photo on screen at most this long (user: B-roll under 3-4 s)
JOIN = {'cut': ('fade', 0.04), 'dissolve': ('fade', 0.25), 'flash': ('fadewhite', 0.18), 'punch': ('zoomin', 0.22)}
MOTIONS = ('push_in', 'pull_out', 'pan_left', 'pan_right', 'hold')
# B-roll effects: Channel Studio's news-brain overlay catalogue (newskit.OVERLAYS), the ones that read on a phone,
# with the brain's own "suits" wording. The words decide; code only caps how many (user: "very few").
EFFECTS = {
    'grain': ('film grain', 'archive or older footage, the past, a reflective moment'),
    'vignette': ('darker corners pulling the eye to the centre', 'a single person, a serious or tense moment'),
    'wash_warm': ('a warm colour wash', 'family, affection, nostalgia, relief'),
    'wash_cool': ('a cool blue wash', 'coldness, tension, police, courts, official settings'),
    'mono': ('black and white', 'the past, a quote from long ago, a death, a sombre moment'),
    'light_leak': ('a warm light sweeping across once', 'celebration, glamour, warmth, a turn in the story'),
    'lens_sweep': ('a diagonal light glint passing once', 'a glossy event, red carpet, an arrival'),
}
# measured news channels: hard cuts are 71-97 % of joins, dissolves/flashes/punches are accents (news_brain.SHARED)
VOICE_SECONDS = (40, 70)  # the voice's accepted length (a profile may change it)
SCRIPT_WORDS = '120 to 150'  # the word range a rejected script is told to fix (a profile may change it)
MAX_JOINS, MAX_EFFECTS = 4, 4  # per Reel: ~18 joins, so 4 accents keeps 78 % cuts; effects never two pieces in a row
VERIFY = None  # optional fn(plan, files, spare, fetch, names) -> files or None, run once all pictures are fetched

EDIT = """You are the editor of a fast, well-edited vertical news Reel (like the best US news pages). The voice-over is fixed and
cut into short pieces. For EACH piece choose:
- moment: the index of the real clip/photo whose description best MATCHES WHAT IS BEING SAID in that piece (main goal).
  Every moment at most once. Prefer clips; the opening piece must be the strongest, most striking moment.
- layout: "band" (picture across the middle over a blurred copy, the classic news look) or "full" (fills the whole
  screen: use for strong close-ups, crowds, places). Switch layouts when the story moves to a new beat.
- motion: push_in, pull_out, pan_left, pan_right or hold. Vary it; photos never sit still; match the energy of the words.
- into: how this piece arrives. "cut" is the normal join (71-97 % of joins on the measured news channels); the others
  are accents, at most {max_joins} in the whole Reel, only where the words call for one: "dissolve" (a new beat or a turn
  in the story, softer), "flash" (a reveal, a big number), "punch" (emphasis on a key word). The first piece is "cut".
- effect: null or ONE B-roll effect when the sentence calls for it, at most {max_effects} in the whole Reel, never two
  pieces in a row, varied: {effects}.
- overlay: null, "fact0"/"fact1" (show that fact box exactly when that fact is spoken) or "name0".."name3" (a name
  title the first time that person/place is mentioned, ONLY on a moment that shows that very person/place). Most
  pieces have no overlay; never two in a row.
Editing must be present throughout (moves, layouts, the few accents spread out), but most pieces are a clean cut with
no effect: the sentence decides, no quotas.

PIECES (index | seconds | words spoken during it):
{pieces}

MOMENTS (index | kind | what it shows):
{moments}

FACTS: {facts}
NAMES: {names}

Return JSON: {{"pieces": [{{"i": <piece index>, "moment": int, "layout": str, "motion": str, "into": str, "effect": str|null, "overlay": str|null}}, ...]}}"""


def _even(x):
    return int(x) // 2 * 2


def split_pieces(lines, timing, total):
    """The voice cut into pieces of at most MAX_PIECE s on ONE continuous timeline (a cut lands on a sentence boundary
    when one falls 2-3 s after the last cut), each with the words spoken during it. Fewer, fuller pieces than cutting
    every sentence separately: a 52 s voice needs ~18 different moments instead of 24."""
    words = []  # (start, end, word) for every spoken word, spread over its sentence by letters
    for (a, b), line in zip(timing, lines):
        ws = line.split()
        n = sum(len(w) + 1 for w in ws) or 1
        t = a
        for w in ws:
            d = (b - a) * (len(w) + 1) / n
            words.append((t, t + d, w))
            t += d
    bounds = sorted({a - GAP / 2 for a, _ in timing[1:]})
    cuts, last = [0.0], 0.0
    while total - last > MAX_PIECE:
        near = [x for x in bounds if last + 2.0 <= x <= last + MAX_PIECE]
        last = near[-1] if near else last + MAX_PIECE
        cuts.append(last)
    if total - cuts[-1] < 1.0 and len(cuts) > 1:  # no sliver at the end: the last two pieces share the remainder
        cuts[-1] = (cuts[-2] + total) / 2
    cuts.append(total)
    out = []
    for a, b in zip(cuts, cuts[1:]):
        text = ' '.join(w for s_, e_, w in words if s_ < b and e_ > a) or ' '.join(lines)
        out.append({'a': a, 'b': b, 'text': text})
    return out


def _overlap(a, b):
    wa, wb = set(re.findall(r'[a-z0-9]+', a.lower())), set(re.findall(r'[a-z0-9]+', b.lower()))
    return len(wa & wb)


def overlay_ok(overlay, moment, facts, names, prev):
    """The text a piece may carry: never on two pieces in a row, and a name title only over a PHOTO whose description
    shows that name (Reel 2 put "BEARS" over a Patriots player and a stadium name over a close-up)."""
    if not overlay or prev:
        return None
    if overlay in ('fact0', 'fact1'):
        return overlay if int(overlay[-1]) < len(facts) else None
    if overlay not in ['name{}'.format(k) for k in range(len(names))] or moment.get('kind') == 'clip':
        return None  # clips often carry the broadcaster's own name caption (ABC7's sat under ours on the first live Reel)
    desc = set(re.findall(r'[a-z0-9]+', moment.get('desc', '').lower()))
    words = [w for w in re.findall(r'[a-z0-9]+', names[int(overlay[4:])].lower()) if len(w) >= 3]
    return overlay if words and any(w in desc for w in words) else None


def edit_plan(pieces, found, facts, names):
    """The AI edit, then the hard rules in code: valid values, every moment once (an unused, best-matching moment
    replaces a duplicate), no overlay twice in a row, at most MAX_JOINS non-cut joins and MAX_EFFECTS effects (the
    brain's earliest ones kept), never an effect on two pieces in a row."""
    plan, by_i = [], {}
    for _ in range(2):  # a second ask when the reply missed pieces
        out = ai.gemini(EDIT.format(
            pieces='\n'.join('{} | {:.1f} | {}'.format(i, p['b'] - p['a'], p['text']) for i, p in enumerate(pieces)),
            moments='\n'.join('{} | {} | {}'.format(i, m['kind'], m.get('desc', '')) for i, m in enumerate(found)),
            facts=json.dumps(facts), names=json.dumps(names), max_joins=MAX_JOINS, max_effects=MAX_EFFECTS,
            effects='; '.join('"{}" = {} (suits: {})'.format(k, v[0], v[1]) for k, v in EFFECTS.items())),
            temperature=0.5, chain='bulk')  # AIUnavailable propagates: the Reel waits, it is never built unedited
        plan = out.get('pieces', []) if isinstance(out, dict) else out
        by_i = {d.get('i'): d for d in plan if isinstance(d, dict)} if isinstance(plan, list) else {}
        if len(by_i) >= len(pieces):
            break
    if len(by_i) < 0.9 * len(pieces):  # 2026-10-23: a failed edit AI shipped 19 plain cuts with keyword-matched pictures
        raise ai.AIUnavailable('edit plan covered {} of {} pieces'.format(len(by_i), len(pieces)))
    used, result, last_overlay = set(), [], None
    for i, p in enumerate(pieces):
        d = by_i.get(i, {})
        m = d.get('moment')
        if not isinstance(m, int) or not 0 <= m < len(found) or m in used:
            free = [k for k in range(len(found)) if k not in used]
            if not free:
                return None  # not enough different moments: the Reel is skipped rather than repeating shots
            m = max(free, key=lambda k: (_overlap(found[k].get('desc', ''), p['text']), found[k]['kind'] == 'clip'))
        used.add(m)
        overlay = overlay_ok(d.get('overlay'), found[m], facts, names, last_overlay)
        last_overlay = overlay
        into = 'cut' if i == 0 else (d.get('into') if d.get('into') in JOIN else 'cut')
        if into != 'cut' and sum(r['into'] != 'cut' for r in result) >= MAX_JOINS:
            into = 'cut'
        effect = d.get('effect') if d.get('effect') in EFFECTS else None
        if effect and (sum(bool(r['effect']) for r in result) >= MAX_EFFECTS or (result and result[-1]['effect'])):
            effect = None
        result.append(dict(p, moment=found[m],
                           layout=d.get('layout') if d.get('layout') in ('band', 'full') else ('band' if i % 4 < 2 else 'full'),
                           motion=d.get('motion') if d.get('motion') in MOTIONS else MOTIONS[i % 4],
                           into=into, effect=effect, overlay=overlay))
    return result


def _motion_crop(motion, w, h, frames):
    """A crop window that moves over a picture pre-scaled 12% larger than (w, h): the camera move of a piece."""
    W, H = _even(w * 1.12), _even(h * 1.12)
    N = max(1, frames - 1)
    f = 'min(n/{},1)'.format(N)  # crop's frame counter is n; zoompan's is on
    x, y = '(iw-ow)/2', '(ih-oh)/2'
    if motion == 'pan_left':
        x = '(iw-ow)*(1-{})'.format(f)
    elif motion == 'pan_right':
        x = '(iw-ow)*{}'.format(f)
    if motion in ('push_in', 'pull_out'):
        # zoompan keeps the frame size while the zoom changes; works per frame on clips (d=1) and stills alike
        fo = 'min(on/{},1)'.format(N)
        z = '1+0.10*{}'.format(fo) if motion == 'push_in' else '1.10-0.10*{}'.format(fo)
        return 'scale={}:{},zoompan=z=\'{}\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d=1:s={}x{}:fps=30'.format(W, H, z, w, h)
    return "scale={}:{},crop={}:{}:x='{}':y='{}'".format(W, H, w, h, x, y)


FX_FILTER = {  # effects that are a plain filter on the finished piece
    'grain': 'noise=alls=12:allf=t',
    'vignette': 'vignette',
    # washes shift the colour planes through a lookup table (colorbalance converts to RGB: measured 3x a plain piece)
    'wash_warm': "lutyuv=u='val-7':v='val+7'",
    'wash_cool': "lutyuv=u='val+10':v='val-6'",
    'mono': 'hue=s=0',
}


def _fx_png(name, folder):
    """The light textures (made once per folder, deterministic): 'leak' a warm orange-white glow (newskit .ov-leak / the
    flash join's .leak), 'sweep' a 115-degree white glint band (newskit .ov-sweep)."""
    path = os.path.join(folder, 'fx_{}.png'.format(name))
    if not os.path.exists(path):
        import numpy as np
        from PIL import Image
        y, x = np.mgrid[0:1920, 0:1080].astype(np.float32)
        rgba = np.zeros((1920, 1080, 4), np.float32)
        if name == 'leak':
            d = np.sqrt(((x - 540) / 650) ** 2 + ((y - 960) / 1150) ** 2)
            k = np.clip(d / 0.75, 0, 1)[..., None]
            rgba[..., :3] = (1 - k) * np.array([255, 240, 215]) + k * np.array([255, 150, 70])
            rgba[..., 3] = 255 * 0.55 * np.clip(1 - d, 0, 1) ** 1.3  # 0.85 washed a face out (frame check)
        else:
            a = np.radians(115)
            dist = np.abs((x - 540) * np.cos(a) + (y - 960) * np.sin(a))
            rgba[..., :3] = 255
            rgba[..., 3] = 255 * 0.55 * np.clip(1 - dist / 140, 0, 1)
        tmp = path + '.{}.tmp.png'.format(os.getpid())
        Image.fromarray(rgba.astype(np.uint8), 'RGBA').save(tmp)
        os.replace(tmp, path)
    return path


def _fx(effect, flash_in, dur, folder):
    """(extra ffmpeg inputs, graph from [base] to [v]) for a piece's effect and the light of a flash join into it."""
    if effect in FX_FILTER:
        return [], '[base]{},format=yuv420p[v]'.format(FX_FILTER[effect])
    png = {'light_leak': 'leak', 'lens_sweep': 'sweep'}.get(effect) or ('leak' if flash_in else None)
    if not png:
        return [], '[base]format=yuv420p[v]'
    inp = ['-loop', '1', '-framerate', '30', '-t', '{:.2f}'.format(dur + 0.1), '-i', _fx_png(png, folder)]
    if effect == 'light_leak':  # sweeps across once, centred at 0.8 s (newskit: in 0.8 s, out 0.8 s)
        g = "[1:v]format=rgba[fx];[base][fx]overlay=x='-w+min(t/{s:.2f},1)*(W+w)':y=0:eval=frame:shortest=1:enable='lt(t,{s:.2f})'".format(s=min(1.6, dur))
    elif effect == 'lens_sweep':  # passes once after 0.15 s (newskit: 1.1 s or 60 % of the shot)
        g = "[1:v]format=rgba[fx];[base][fx]overlay=x='-w+min(max(t-0.15,0)/{s:.2f},1)*(W+w)':y=0:eval=frame:shortest=1:enable='lt(t,{e:.2f})'".format(
            s=min(1.1, dur * 0.6), e=0.15 + min(1.1, dur * 0.6))
    else:  # flash join: the warm leak burns out over the first 0.4 s of the incoming picture
        g = '[1:v]format=rgba,fade=t=out:st=0.05:d=0.35:alpha=1[fx];[base][fx]overlay=0:0:shortest=1:enable=\'lt(t,0.45)\''
    return inp, g + ',format=yuv420p[v]'


def render_piece(src, kind, layout, motion, dur, out, effect=None, flash_in=False, cx=None):
    """One 1080x1920 piece. band: picture across the middle over a blurred, darkened copy; full: fills the frame.
    effect: one of EFFECTS on the finished piece; flash_in: the piece arrives by a flash join (a warm leak burns out).
    cx: where the subject is across a wide picture (0-1, from the vision check): a full-screen crop keeps it in frame."""
    frames = max(2, int(round(dur * FPS)))
    if kind == 'clip':
        inp = ['-i', src]
        pre = 'fps=30,tpad=stop_mode=clone:stop_duration={:.2f}'.format(dur)
    else:
        inp = ['-loop', '1', '-framerate', '30', '-t', '{:.2f}'.format(dur + 0.1), '-i', src]
        pre = 'fps=30'
    if layout == 'full':
        try:
            cx = min(1.0, max(0.0, float(cx))) if cx is not None else None
        except (TypeError, ValueError):
            cx = None
        xs = "max(0,min(iw-ow,iw*{:.3f}-ow/2))".format(cx) if cx is not None else '(iw-ow)/2'
        graph = ('[0:v]{pre},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:x=\'' + xs + '\':y=(ih-oh)/2,{mv},'
                 'eq=brightness=-0.05,trim=duration={d:.2f},setpts=PTS-STARTPTS[base]').format(
            pre=pre, mv=_motion_crop(motion, 1080, 1920, frames), d=dur)
    else:
        from PIL import Image
        if kind == 'clip':
            fw, fh = 1080, _even(1080 * 9 / 16)
        else:
            w, h = Image.open(src).size
            k = min(1080 / w, MEDIA_H / h)
            fw, fh = _even(w * k), _even(h * k)
        bg = ('scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=270:480,boxblur=10:2,'
              'scale=1080:1920,eq=brightness=-0.12')
        fg = 'scale={fw}:{fh}:force_original_aspect_ratio=increase,crop={fw}:{fh},{mv},setsar=1'.format(
            fw=fw, fh=fh, mv=_motion_crop(motion, fw, fh, frames))
        graph = ('[0:v]{pre},split[a][b];[a]{bg}[bg];[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:{top}+({mh}-h)/2,'
                 'trim=duration={d:.2f},setpts=PTS-STARTPTS[base]').format(
            pre=pre, bg=bg, fg=fg, top=MEDIA_TOP, mh=MEDIA_H, d=dur)
    fx_in, fx_graph = _fx(effect, flash_in, dur, os.path.dirname(os.path.abspath(out)))
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y'] + inp + fx_in + [
        '-filter_complex', graph + ';' + fx_graph, '-map', '[v]', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-r', '30', out],
        check=True, capture_output=True, timeout=900)
    return out


def _overlay_ass(headline, page, lines, timing, total, plan, facts, names, path, words=None):
    """Captions plus the fact boxes and name titles where the edit put them (user: no headline, no page name)."""
    captions(lines, timing, path, words)
    txt = open(path).read()
    txt = txt.replace('Style: Cap,', 'Style: Fact,Anton,96,&H00FFFFFF,&H00FFFFFF,&H002E1DE1,&H002E1DE1,0,0,0,0,100,100,1,0,3,18,0,5,80,80,0,1\n'
                      'Style: Name,Anton,86,&H000B0B0B,&H000B0B0B,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,1,0,3,14,0,1,70,70,0,1\n'
                      'Style: Cap,')
    for p in plan:
        ov = p.get('overlay')
        if not ov:
            continue
        a, b = p['a'] + 0.15, min(p['b'] + 1.2, total)
        if ov.startswith('fact'):
            text, style, pos = facts[int(ov[-1])], 'Fact', r'{\pos(540,520)\fad(120,160)\fscx92\fscy92\t(0,160,\fscx100\fscy100)}'
        else:
            text, style, pos = names[int(ov[4:])], 'Name', r'{\pos(70,1250)\fad(120,160)\move(30,1250,70,1250,0,200)}'
        txt += 'Dialogue: 2,{},{},{},,0,0,0,,{}{}\n'.format(_ass_time(a), _ass_time(b), style, pos,
                                                           text.upper().replace('{', '(').replace('}', ')'))
    open(path, 'w').write(txt)


def media_key(m):
    return m['url'] if m['kind'] == 'photo' else '{}@{}'.format(m['vid'], int(m['t'] // 10))


def build(page, outlets, story, query, subject, tag, work, used=()):
    """Returns {'video', 'caption', 'headline', 'seconds', 'moments', 'media_keys'} or {'skip': reason}.
    Never makes a text-only Reel and never repeats a shot."""
    from . import moments
    t0 = [time.time()]

    def stage(name):  # how long every step took, so a slowdown is always visible in the log
        print('  [{:5.0f} s] {}'.format(time.time() - t0[0], name), flush=True)
    os.makedirs(work, exist_ok=True)
    src = ai.sources_text(outlets)
    s, problems = None, None
    for _ in range(3):
        prompt = SCRIPT.format(sources=src)
        if problems:
            prompt += ('\n\nYOUR PREVIOUS SCRIPT WAS REJECTED FOR: {}. Fix exactly these problems. Count the words: the lines '
                       'together must have {} words (too short: add context, stakes and what happens next from the sources; '
                       'too long: cut the least important details).').format('; '.join(problems), SCRIPT_WORDS)
        s = ai.gemini(prompt)
        if isinstance(s, list):
            s = s[0] if s and isinstance(s[0], dict) else {'skip': 'no script'}
        if s.get('skip'):
            return {'skip': s['skip']}
        problems = script_problems(s)
        if not problems:
            break
    if problems:
        return {'skip': 'script: {}'.format(problems)}
    names = [n for n in (s.get('names') or []) if isinstance(n, str)][:4] or ([subject] if subject else [])
    facts = [f for f in (s.get('facts') or []) if isinstance(f, str)][:2]
    clips_ok = (tag or '').upper() not in moments.SPORTS_TAGS  # sports: photos only (leagues enforce hardest)
    stage('script')
    # libraries load ONCE here: importing numpy in two threads at once fails ("partially initialized module")
    import numpy  # noqa: F401
    import soundfile  # noqa: F401
    from kokoro_onnx import Kokoro  # noqa: F401
    from PIL import Image  # noqa: F401
    # the voice (CPU) is recorded while the moments are searched and checked (network + AI): same result, less waiting
    with ThreadPoolExecutor(1) as ex:
        v = ex.submit(voice, s['lines'], work)
        found = moments.find(story, names, query, os.path.join(work, 'moments'), clips_ok=clips_ok, used=set(used))
        stage('moments ({})'.format(len(found)))
        wav, timing, total = v.result()
    stage('voice ({:.0f} s)'.format(total))
    if not VOICE_SECONDS[0] <= total <= VOICE_SECONDS[1]:
        return {'skip': 'voice is {:.0f} s, need {}-{}'.format(total, *VOICE_SECONDS)}
    pieces = split_pieces(s['lines'], timing, total)
    if len(found) < len(pieces):
        return {'skip': '{} different real moments for {} pieces (never repeat a shot)'.format(len(found), len(pieces))}
    plan = edit_plan(pieces, found, facts, names)
    if not plan:
        return {'skip': 'edit plan could not give every piece its own moment'}
    stage('edit plan ({} pieces)'.format(len(plan)))
    spare = [m for m in found if all(m is not p['moment'] for p in plan)]
    seg_lens = []
    for n, p in enumerate(plan):
        nxt = plan[n + 1]['into'] if n + 1 < len(plan) else 'cut'
        seg_lens.append(p['b'] - p['a'] + JOIN[nxt][1])  # each piece carries the overlap the next join needs

    def fetch(n, m):
        try:
            if m['kind'] == 'clip':
                f = moments.cut(m['vid'], m['t'], seg_lens[n] + 0.3, os.path.join(work, 'src{:02d}.mp4'.format(n)))
            else:
                f = moments.photo(m['url'], os.path.join(work, 'src{:02d}.jpg'.format(n)))
            if f and moments.text_heavy(f):  # someone else's subtitles/article text: swapped for another moment
                print('  text-heavy, replaced: {}'.format(m.get('desc', '')[:60]), flush=True)
                os.remove(f)
                return None
            return f
        except (OSError, subprocess.SubprocessError) as e:  # one picture's file trouble drops that picture, not the Reel
            import traceback
            print('  source {} dropped ({}: {})'.format(n, type(e).__name__, str(e)[:120]), flush=True)
            traceback.print_exc()
            return None

    with ThreadPoolExecutor(3) as ex:  # 3 downloads at a time (YouTube pacing still applies per request)
        files = list(ex.map(lambda n: fetch(n, plan[n]['moment']), range(len(plan))))
    for n, p in enumerate(plan):  # a moment that could not be fetched is replaced by an unused one
        while not files[n] and spare:
            cand = spare.pop(0)
            files[n] = fetch(n, cand)
            if files[n]:
                p['moment'] = cand
        if not files[n]:
            return {'skip': 'could not fetch enough moments'}
    stage('fetched {} sources'.format(len(files)))
    if VERIFY:  # a profile's final look at every picture together (PROFILE=hi); the US Page sets none
        files = VERIFY(plan, files, spare, fetch, names)
        if not files:
            return {'skip': 'pictures failed the final check and no spares were left'}
        stage('final picture check passed')
    ass = os.path.join(work, 'overlay.ass')
    screen = s.get('screen_lines') if len(s.get('screen_lines') or []) == len(s['lines']) else s['lines']
    _overlay_ass(s['headline'], page, screen, timing, total, plan, facts, names, ass, word_times(wav, screen, timing))
    caption = s['caption'].strip()  # the AI-voice disclosure is Meta's own is_ai_generated flag on publish
    open(os.path.join(work, 'caption.txt'), 'w').write(caption + '\n')
    json.dump({'script': s, 'plan': plan}, open(os.path.join(work, 'edit.json'), 'w'), indent=1, default=str)
    video = os.path.join(work, 'reel.mp4')
    tar = cloud.pack(work, plan, files, seg_lens, wav, ass, total, SFX_DIR)
    if os.environ.get('REEL_RENDER') == 'pack':  # the job is left for a manual Cloud Run render (samples only)
        return {'skip': 'packed for Cloud Run: {}'.format(tar)}
    marks = cloud.render(tar, video)
    if marks:
        stage('rendered + assembled on Cloud Run ({})'.format(marks))
    else:
        rendered = [render_piece(files[n], p['moment']['kind'], p['layout'], p['motion'], seg_lens[n],
                                 os.path.join(work, 'seg{:02d}.mp4'.format(n)), p['effect'], p['into'] == 'flash',
                                 p['moment'].get('cx'))
                    for n, p in enumerate(plan)]
        stage('rendered pieces')
        video = assemble(rendered, plan, wav, ass, total, video)
        stage('assembled')
    kinds = [p['moment']['kind'] for p in plan]
    return {'video': video, 'caption': caption, 'headline': s['headline'], 'seconds': round(total, 1),
            'moments': '{} clips, {} photos, {} joins: {}'.format(kinds.count('clip'), kinds.count('photo'), len(plan) - 1,
                                                                    ', '.join('{}x{}'.format(v, [p['into'] for p in plan].count(v)) for v in JOIN))
                       + '; effects: {}'.format(', '.join('{}@{}'.format(p['effect'], n) for n, p in enumerate(plan) if p['effect']) or 'none'),
            'media_keys': [media_key(p['moment']) for p in plan]}


SFX_DIR = os.path.expanduser(os.environ.get('SFX_DIR', '~/sfx'))  # Meta Sound Collection files: kept OFF the public repo
SFX_MAX, SFX_GAP = 6, 4.0  # user: never flood a Reel; only where the edit motivates a sound, spread out
SFX_RULES = [  # (priority, trigger, file prefix, lead seconds before the moment, level in dB under full scale)
    (1, 'flash', 'impact', 0.05, -13),   # a white-flash reveal
    (2, 'fact', 'click', 0.0, -17),      # a fact box popping on screen
    (3, 'punch', 'whoosh', 0.30, -16),   # a punch-in movement
    (4, 'name_photo', 'shutter', 0.0, -18),  # a name title over that person's photo
]


_AUDIBLE = {}


def _audible(path):
    """A silent effect file is skipped and reported (a fade-at-0 conversion once made every effect silent)."""
    if path not in _AUDIBLE:
        r = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True)
        m = re.search(r'max_volume: (-?[\d.]+|-inf) dB', r.stderr)
        _AUDIBLE[path] = bool(m) and m.group(1) != '-inf' and float(m.group(1)) > -30
        if not _AUDIBLE[path]:
            print('  sound effect is silent, skipped: {}'.format(os.path.basename(path)), flush=True)
    return _AUDIBLE[path]


def sfx_events(plan):
    """Motivated sound effects only (no sound on plain cuts/dissolves), at most SFX_MAX per Reel, SFX_GAP s apart,
    higher-priority moments first; files of one kind rotate so the same sound never repeats back to back."""
    if not os.path.isdir(SFX_DIR):
        return []
    files = sorted(f for f in os.listdir(SFX_DIR) if f.endswith('.wav') and _audible(os.path.join(SFX_DIR, f)))
    cands = []
    for p in plan:
        if p.get('into') in ('flash', 'punch'):
            cands.append((p['a'], p['into']))
        ov = p.get('overlay') or ''
        if ov.startswith('fact'):
            cands.append((p['a'] + 0.15, 'fact'))
        elif ov.startswith('name') and p['moment'].get('kind') == 'photo':
            cands.append((p['a'] + 0.15, 'name_photo'))
    rules = {r[1]: r for r in SFX_RULES}
    chosen = []
    for t, trig in sorted(cands, key=lambda c: (rules[c[1]][0], c[0])):
        if len(chosen) >= SFX_MAX or any(abs(t - c[0]) < SFX_GAP for c in chosen):
            continue
        chosen.append((t, trig))
    out, used = [], {}
    for t, trig in sorted(chosen):
        _, _, prefix, lead, level = rules[trig]
        pool = [f for f in files if f.startswith(prefix)]
        if not pool:
            continue
        k = used.get(prefix, 0)
        used[prefix] = k + 1
        out.append((max(0.0, t - lead), os.path.join(SFX_DIR, pool[k % len(pool)]), level))
    return out


def assemble(rendered, plan, wav, ass, total, video):
    """Joins (xfade chain: the join into piece k+1 starts where piece k ends on the final timeline), overlays, voice."""
    inputs, chain, last, t_end = [], [], '[0:v]', 0.0
    for f in rendered:
        inputs += ['-i', f]
    for k in range(1, len(rendered)):
        t_end += plan[k - 1]['b'] - plan[k - 1]['a']
        trans, td = JOIN[plan[k]['into']]
        out = '[x{}]'.format(k)
        chain.append('{}[{}:v]xfade=transition={}:duration={:.2f}:offset={:.3f}{}'.format(last, k, trans, td, t_end, out))
        last = out
    graph = ';'.join(chain) + (';' if chain else '') + '{}subtitles={}:fontsdir={},format=yuv420p[v]'.format(last, ass, FONTS)
    # audio: the voice, plus the motivated sound effects each delayed to its moment and set well under the voice
    sfx = sfx_events(plan)
    n_v = len(rendered)
    audio_in = ['-i', wav]
    mix = ['[{}:a]aformat=sample_rates=48000:channel_layouts=stereo[vo]'.format(n_v)]
    for k, (t, f, level) in enumerate(sfx):
        audio_in += ['-i', f]
        ms = int(t * 1000)
        mix.append('[{}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={}dB,adelay={}|{}[s{}]'.format(n_v + 1 + k, level + 1, ms, ms, k))
    if sfx:
        mix.append('[vo]{}amix=inputs={}:normalize=0:duration=first,alimiter=limit=0.95[a]'.format(
            ''.join('[s{}]'.format(k) for k in range(len(sfx))), len(sfx) + 1))
    else:
        mix.append('[vo]anull[a]')
    graph += ';' + ';'.join(mix)
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y'] + inputs + audio_in + ['-filter_complex', graph,
                    '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                    '-g', str(FPS * 2), '-keyint_min', str(FPS * 2),  # Meta: closed GOP of 2-5 s
                    '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-ar', '48000',  # Meta: AAC stereo 48 kHz >= 128 kbps
                    '-t', '{:.2f}'.format(total), '-movflags', '+faststart', video],
                   check=True, capture_output=True, timeout=2400)
    return video
