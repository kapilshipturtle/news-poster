"""45-60 s vertical news Reels (1080x1920): AI script -> Kokoro voice line by line -> scene cards (Chrome)
-> ffmpeg slow zoom per scene + burned-in captions. Voice only, no music."""
import html
import json
import os
import re
import subprocess

from . import ai
from .render import FONTS

VOICE = os.environ.get('REEL_VOICE', 'af_heart')
MODELS = os.path.expanduser(os.environ.get('KOKORO_DIR', '~/models'))
FPS = 30
GAP = 0.22  # pause between spoken lines, seconds

SCRIPT = """You write the voice-over for a 45-60 second vertical news Reel for a US Facebook news page (the page name is not part of the story; never mention it).
The story is whatever the SOURCES below report.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names, scenes or outcomes.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

Return JSON: {{"headline": str, "lines": [str, ...], "facts": [str, str], "names": [str], "caption": str}}
- lines: 9 to 13 spoken sentences, 120-150 words in total, each at most 16 words. Written for the ear: short, clear,
  contractions, plain everyday words, no jargon, no abbreviations a narrator would stumble on (write "percent", "million").
  The lines are the VOICE: spell numbers out for the ear there.
  Line 1 (max 10 words) is the hook: the most surprising or emotional REAL detail, so a scroller stops in 2 seconds.
  Then what happened, why it matters to an ordinary American, and the human stakes.
  The LAST line is one specific question people will disagree about (never "comment below", "like if", "share").
- headline: max 7 words, punchy, factual, shown at the top of the screen the whole time. Use DIGITS and symbols here ($25 billion, 97-71), never spelled-out numbers.
- facts: two striking concrete details from the sources, max 9 words each, digits as in the headline.
- names: the people, places and organisations the story is about, exactly as the sources write them (max 4).
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


MEDIA_TOP, MEDIA_H = 600, 760  # the picture band; the headline sits above it, the captions below


def _even(x):
    return int(x) // 2 * 2


def _segment(src, kind, dur, out):
    """One 1080x1920 piece: the clip/photo fitted into the band over a blurred, darkened copy of itself."""
    bg = ('scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=270:480,boxblur=10:2,'
          'scale=1080:1920,eq=brightness=-0.10')
    if kind == 'clip':
        fg = 'scale=1080:{}:force_original_aspect_ratio=decrease,setsar=1'.format(MEDIA_H)
        graph = ('[0:v]fps=30,tpad=stop_mode=clone:stop_duration={d},split[a][b];[a]{bg}[bg];[b]{fg}[fg];'
                 '[bg][fg]overlay=(W-w)/2:{top}+({h}-h)/2,trim=duration={d},setpts=PTS-STARTPTS,format=yuv420p[v]').format(
            d=dur, bg=bg, fg=fg, top=MEDIA_TOP, h=MEDIA_H)
        inp = ['-i', src]
    else:
        from PIL import Image
        w, h = Image.open(src).size
        k = min(1080 / w, MEDIA_H / h)
        fw, fh = _even(w * k), _even(h * k)
        # slow pan across a slightly enlarged photo (works on ffmpeg 4.2 too, and is cheaper than zoompan)
        fg = ("scale={sw}:{sh},crop={fw}:{fh}:x='(iw-ow)*t/{d}':y='(ih-oh)/2',setsar=1").format(
            sw=_even(fw * 1.08), sh=_even(fh * 1.08), fw=fw, fh=fh, d=dur)
        graph = ('[0:v]fps=30,split[a][b];[a]{bg}[bg];[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:{top}+({h}-h)/2,'
                 'trim=duration={d},setpts=PTS-STARTPTS,format=yuv420p[v]').format(d=dur, bg=bg, fg=fg, top=MEDIA_TOP, h=MEDIA_H)
        inp = ['-loop', '1', '-t', '{:.2f}'.format(dur + 0.1), '-i', src]
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y'] + inp + [
        '-filter_complex', graph, '-map', '[v]', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', out],
        check=True, capture_output=True, timeout=900)
    return out


def _overlay_ass(headline, page, lines, timing, total, path):
    """Captions under the picture, the headline above it for the whole Reel, the page name at the bottom."""
    captions(lines, timing, path)
    txt = open(path).read()
    txt = txt.replace('Style: Cap,Anton,104,', 'Style: Head,Anton,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,7,3,8,70,70,170,1\n'
                      'Style: Brand,Anton,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,4,2,2,60,60,90,1\n'
                      'Style: Cap,Anton,104,')
    txt = txt.replace('Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Head', 'Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Head')
    txt = txt.replace(',2,60,60,300,1', ',2,60,60,330,1')  # captions sit just under the picture band
    end = _ass_time(total)
    txt += 'Dialogue: 1,0:00:00.00,{},Head,,0,0,0,,{}\n'.format(end, headline.upper().replace('{', '(').replace('}', ')'))
    txt += 'Dialogue: 1,0:00:00.00,{},Brand,,0,0,0,,{}\n'.format(end, page.upper())
    open(path, 'w').write(txt)


def plan_visuals(timing, total, found):
    """One real moment per spoken line (two for a line longer than 5.5 s); a moment is used at most twice."""
    pieces = []
    for i, (a, b) in enumerate(timing):
        start = 0.0 if i == 0 else a - GAP / 2
        end = total if i == len(timing) - 1 else timing[i + 1][0] - GAP / 2
        if end - start > 5.5:
            mid = (start + end) / 2
            pieces += [(start, mid), (mid, end)]
        else:
            pieces.append((start, end))
    out, uses = [], {}
    k = 0
    for a, b in pieces:
        for _ in range(len(found)):
            m = found[k % len(found)]
            k += 1
            key = m.get('vid', m.get('url')) + str(m.get('t', ''))
            if uses.get(key, 0) < 2:
                uses[key] = uses.get(key, 0) + 1
                out.append((a, b, m))
                break
    return out


def build(page, outlets, story, query, subject, tag, work):
    """Returns {'video', 'caption', 'headline', 'seconds', 'moments'} or {'skip': reason}. Never makes a text-only Reel."""
    from . import moments
    os.makedirs(work, exist_ok=True)
    src = '\n\n'.join('[{}] {}\n{}'.format(a['outlet'], a['title'], a['text']) for a in outlets)
    s, problems = None, None
    for _ in range(2):
        prompt = SCRIPT.format(sources=src)
        if problems:
            prompt += '\n\nYOUR PREVIOUS SCRIPT WAS REJECTED FOR: {}. Fix exactly these problems.'.format('; '.join(problems))
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
    found = moments.find(story, names, query, os.path.join(work, 'moments'))
    clips = [m for m in found if m['kind'] == 'clip']
    if len(found) < 3:
        return {'skip': 'only {} real moments found'.format(len(found))}
    json.dump(dict(s, tag=tag, moments=found), open(os.path.join(work, 'reel_script.json'), 'w'), indent=1)
    wav, timing, total = voice(s['lines'], work)
    if not 40 <= total <= 70:
        return {'skip': 'voice is {:.0f} s, need 45-60'.format(total)}
    parts, n = [], 0
    for a, b, m in plan_visuals(timing, total, found):
        dur = round(b - a, 2)
        n += 1
        src_file = None
        if m['kind'] == 'clip':
            src_file = moments.cut(m['vid'], m['t'], min(dur + 0.6, 6.0), os.path.join(work, 'src{:02d}.mp4'.format(n)))
        else:
            src_file = moments.photo(m['url'], os.path.join(work, 'src{:02d}.jpg'.format(n)))
        if not src_file:  # a moment that cannot be fetched gives its time to the next piece
            if parts:
                parts[-1] = (parts[-1][0], parts[-1][1] + dur, parts[-1][2], parts[-1][3])
            continue
        parts.append((src_file, dur, m['kind'], n))
    if len(parts) < 3:
        return {'skip': 'only {} moments could be fetched'.format(len(parts))}
    pieces = []
    for src_file, dur, kind, i in parts:
        pieces.append(_segment(src_file, kind, dur, os.path.join(work, 'seg{:02d}.mp4'.format(i))))
    lst = os.path.join(work, 'pieces.txt')
    open(lst, 'w').write(''.join("file '{}'\n".format(p) for p in pieces))
    ass = os.path.join(work, 'overlay.ass')
    _overlay_ass(s['headline'], page, s['lines'], timing, total, ass)
    video = os.path.join(work, 'reel.mp4')
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-i', wav,
                    '-vf', 'subtitles={}:fontsdir={}'.format(ass, FONTS), '-map', '0:v', '-map', '1:a',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-g', str(FPS * 2), '-keyint_min', str(FPS * 2),
                    '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-ar', '48000', '-shortest', '-movflags', '+faststart', video],
                   check=True, capture_output=True, timeout=1800)
    caption = s['caption'].strip()  # the AI-voice disclosure is Meta's own is_ai_generated flag on publish
    open(os.path.join(work, 'caption.txt'), 'w').write(caption + '\n')
    return {'video': video, 'caption': caption, 'headline': s['headline'], 'seconds': round(total, 1),
            'moments': '{} clips, {} photos'.format(sum(1 for p in parts if p[2] == 'clip'), sum(1 for p in parts if p[2] != 'clip'))}
