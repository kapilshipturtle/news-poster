"""45-60 s vertical news Reels (1080x1920): AI script -> Kokoro voice line by line -> scene cards (Chrome)
-> ffmpeg slow zoom per scene + burned-in captions. Voice only, no music."""
import html
import json
import os
import re
import subprocess

from . import ai
from .render import CHROME, FONTS, _save

VOICE = os.environ.get('REEL_VOICE', 'af_heart')
MODELS = os.path.expanduser(os.environ.get('KOKORO_DIR', '~/models'))
FPS = 30
GAP = 0.22  # pause between spoken lines, seconds

SCRIPT = """You write the voice-over for a 45-60 second vertical news Reel on "{page}", a US Facebook news page.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names, scenes or outcomes.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

Return JSON: {{"headline": str, "lines": [str, ...], "facts": [str, str], "caption": str}}
- lines: 9 to 13 spoken sentences, 120-150 words in total, each at most 16 words. Written for the ear: short, clear,
  contractions, plain everyday words, no jargon, no abbreviations a narrator would stumble on (write "percent", "million").
  Line 1 (max 10 words) is the hook: the most surprising or emotional REAL detail, so a scroller stops in 2 seconds.
  Then what happened, why it matters to an ordinary American, and the human stakes.
  The LAST line is one specific question people will disagree about (never "comment below", "like if", "share").
- headline: max 7 words, punchy, factual (shown big on the opening card).
- facts: two striking concrete details from the sources, max 9 words each (shown as cards).
- caption: the Reel's description, 15-40 words, emotional but factual, no hashtags, no credits, no links.
No em dashes. Never use: delve, tapestry, testament, game-changer, buckle up, let that sink in, breaking news.

SOURCES:
{sources}"""

CSS = '''@font-face{{font-family:Anton;src:url(file://{f}/anton-400.woff2)}}
@font-face{{font-family:Inter;src:url(file://{f}/inter.woff2);font-weight:100 900}}
*{{margin:0;box-sizing:border-box}}html,body{{width:1080px;height:1920px;overflow:hidden;font-family:Inter,sans-serif}}
body{{background:#fff;position:relative}}
.tag{{display:inline-block;background:#e11d2e;color:#fff;font:800 34px Inter;letter-spacing:3px;padding:12px 22px;border-radius:8px}}
.brand{{position:absolute;left:0;right:0;bottom:70px;text-align:center;font:800 34px Inter;color:#0b0b0b;letter-spacing:1px}}
.brand span{{color:#e11d2e}}
.hook{{position:absolute;left:70px;right:70px;top:330px}}
.hook .hl{{font:150px/1.0 Anton;text-transform:uppercase;color:#0b0b0b;margin-top:34px;border-left:18px solid #e11d2e;padding-left:40px}}
.top{{position:absolute;left:60px;right:60px;top:110px;font:76px/1.04 Anton;text-transform:uppercase;color:#0b0b0b}}
.emb{{position:absolute;left:50px;right:50px;top:380px;height:1000px;display:flex;align-items:flex-start;justify-content:center}}
.emb img{{max-width:100%;max-height:100%;object-fit:contain;border-radius:18px;box-shadow:0 14px 50px rgba(0,0,0,.14)}}
.full{{position:absolute;inset:0;width:1080px;height:1920px;object-fit:cover;object-position:center 25%}}
.shade{{position:absolute;inset:0;background:linear-gradient(rgba(0,0,0,.72),rgba(0,0,0,0) 30%,rgba(0,0,0,0) 55%,rgba(0,0,0,.75))}}
.photo .top{{color:#fff}}.photo .brand{{color:#fff}}
.fact{{position:absolute;left:80px;right:80px;top:470px;font:128px/1.02 Anton;text-transform:uppercase;color:#0b0b0b}}
.fact b{{color:#e11d2e;font-weight:400}}
.outro{{position:absolute;left:80px;right:80px;top:420px;font:112px/1.04 Anton;text-transform:uppercase;color:#0b0b0b}}
.outro small{{display:block;font:700 46px Inter;text-transform:none;color:#e11d2e;margin-top:46px}}'''


def script(page, outlets):
    src = '\n\n'.join('[{}] {}\n{}'.format(a['outlet'], a['title'], a['text']) for a in outlets)
    out = ai.gemini(SCRIPT.format(page=page, sources=src))
    if isinstance(out, list):
        out = out[0] if out and isinstance(out[0], dict) else {'skip': 'model returned no script'}
    return out


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


def _card(kind, work, name, page, **k):
    brand = '<div class="brand"><span>●</span> {}</div>'.format(html.escape(page))
    e = {key: html.escape(v) if isinstance(v, str) else v for key, v in k.items()}
    if kind == 'hook':
        body = '<div class="hook"><span class="tag">{tag}</span><div class="hl">{headline}</div></div>'.format(**e) + brand
    elif kind == 'tweet':
        body = '<div class="top">{headline}</div><div class="emb"><img src="file://{png}"></div>'.format(**e) + brand
    elif kind == 'photo':
        body = ('<img class="full" src="{}"><div class="shade"></div><div class="top">{}</div>'.format(k['url'], e['headline'])
                + brand)
    elif kind == 'fact':
        body = '<div class="fact">{}</div>'.format(e['text']) + brand
    else:  # outro
        body = '<div class="outro">{}<small>Follow {} for more</small></div>'.format(e['text'], html.escape(page)) + brand
    page_html = os.path.join(work, name + '.html')
    png = os.path.join(work, name + '.png')
    open(page_html, 'w').write('<!doctype html><html><head><meta charset="utf-8"><style>{}</style></head>'
                               '<body class="{}">{}</body></html>'.format(CSS.format(f=FONTS), kind, body))
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars', '--force-device-scale-factor=1',
                    '--window-size=1080,1920', '--virtual-time-budget=10000', '--screenshot=' + png, 'file://' + page_html],
                   check=True, capture_output=True, timeout=300)
    return png


def _ass_time(t):
    return '{:d}:{:02d}:{:05.2f}'.format(int(t // 3600), int(t % 3600 // 60), t % 60)


def captions(lines, timing, path):
    """Burned-in captions, 2-4 words at a time, each chunk's time split by its share of the line's letters."""
    ev = []
    for line, (a, b) in zip(lines, timing):
        words = line.split()
        chunks, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) >= 3 or (len(cur) >= 2 and w[-1] in ',.?!:;'):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        total = sum(len(' '.join(c)) for c in chunks)
        t = a
        for c in chunks:
            d = (b - a) * len(' '.join(c)) / total
            text = ' '.join(c).upper().replace('{', '(').replace('}', ')')
            ev.append('Dialogue: 0,{},{},Cap,,0,0,0,,{}'.format(_ass_time(t), _ass_time(t + d), text))
            t += d
    open(path, 'w').write('''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,8,3,2,60,60,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
''' + '\n'.join(ev) + '\n')


def scenes(s, timing, tweets, photo_url):
    """Which card is on screen when: hook for the first 2 lines, then the story's visuals in order, the question last."""
    visuals = []
    if tweets:
        visuals.append(('tweet', {'png': tweets[0]['png'], 'headline': s['headline']}))
    if photo_url:
        visuals.append(('photo', {'url': photo_url, 'headline': s['headline']}))
    for f in (s.get('facts') or [])[:2]:
        visuals.append(('fact', {'text': f}))
    n = len(timing)
    middle = list(range(2, n - 1))
    plan = [('hook', {'tag': s.get('tag', 'NEWS'), 'headline': s['headline']}, [0, 1])]
    if visuals and middle:
        per = max(1, -(-len(middle) // len(visuals)))
        for i, v in enumerate(visuals):
            idx = middle[i * per:(i + 1) * per]
            if idx:
                plan.append((v[0], v[1], idx))
    plan.append(('outro', {'text': s['lines'][-1]}, [n - 1]))
    return plan


def build(page, outlets, tweets, photo_url, tag, work):
    """Returns {'video', 'caption', 'headline', 'seconds'} or {'skip': reason}."""
    os.makedirs(work, exist_ok=True)
    s, problems = None, None
    for _ in range(2):
        s = script(page, outlets) if problems is None else ai.gemini(
            SCRIPT.format(page=page, sources='\n\n'.join('[{}] {}\n{}'.format(a['outlet'], a['title'], a['text']) for a in outlets))
            + '\n\nYOUR PREVIOUS SCRIPT WAS REJECTED FOR: {}. Fix exactly these problems.'.format('; '.join(problems)))
        if isinstance(s, list):
            s = s[0] if s and isinstance(s[0], dict) else {'skip': 'no script'}
        if s.get('skip'):
            return {'skip': s['skip']}
        problems = script_problems(s)
        if not problems:
            break
    if problems:
        return {'skip': 'script: {}'.format(problems)}
    s['tag'] = tag
    json.dump(s, open(os.path.join(work, 'reel_script.json'), 'w'), indent=1)
    wav, timing, total = voice(s['lines'], work)
    if not 40 <= total <= 70:
        return {'skip': 'voice is {:.0f} s, need 45-60'.format(total)}
    if photo_url:
        photo_url = _save(photo_url, os.path.join(work, 'subject.jpg'))
    plan = scenes(s, timing, tweets, photo_url)
    inputs, filters, cuts = [], [], []
    for i, (kind, kw, idx) in enumerate(plan):
        start = 0 if i == 0 else timing[idx[0]][0] - GAP / 2
        end = total if i == len(plan) - 1 else timing[plan[i + 1][2][0]][0] - GAP / 2
        png = _card(kind, work, 'scene{:02d}'.format(i), page, **kw)
        frames = max(1, int(round((end - start) * FPS)))
        inputs += ['-i', png]
        filters.append('[{i}]scale=1080:1920,zoompan=z=\'min(zoom+0.0006,1.10)\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\''
                       ':d={f}:s=1080x1920:fps={fps}[v{i}]'.format(i=i, f=frames, fps=FPS))
        cuts.append('[v{}]'.format(i))
    ass = os.path.join(work, 'captions.ass')
    captions(s['lines'], timing, ass)
    video = os.path.join(work, 'reel.mp4')
    graph = ';'.join(filters) + ';{}concat=n={}:v=1:a=0,format=yuv420p,subtitles={}:fontsdir={}[v]'.format(
        ''.join(cuts), len(cuts), ass, FONTS)
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y'] + inputs + ['-i', wav, '-filter_complex', graph,
                    '-map', '[v]', '-map', '{}:a'.format(len(plan)), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                    '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', video],
                   check=True, capture_output=True, timeout=1800)
    caption = s['caption'].strip() + '\n\nAI voice narration.'
    open(os.path.join(work, 'caption.txt'), 'w').write(caption + '\n')
    return {'video': video, 'caption': caption, 'headline': s['headline'], 'seconds': round(total, 1)}
