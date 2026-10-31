"""Hindi viral-people videos (Indian Daily News, user 2026-10-31): The Shiva Kumar's measured template.
1440x1920 (3:4) maroon #7A1616; one headline sentence over 4 lines (white/yellow, ink tops 169/343 and 1378/1591);
a 1440x800 media window at y=565; a hook card per teaser line (picture left, big white text right) with a whoosh-boom;
the story in clips/photos cut on sentences; one ~2.5 s burst of a clip's own sound; voice only (no music).
job.json: {type: 'viral', headline: [4], cards: [{img, lines}], shots: [{src, start, dur, still}], burst: {src, start,
dur, after_shot}, hits: [seconds], total}. Needs Pillow with raqm (Devanagari shaping)."""
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont

W, H, MY, MH = 1440, 1920, 565, 800
BG = (122, 22, 22)
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'fonts', 'NotoSansDevanagari-ExtraBold.ttf')
# the Devanagari font has no Latin letters: "CEO", "Bugatti", "GPS" came out as boxes (test 8) - his text mixes English in
LATIN = os.path.join(os.path.dirname(FONT), 'Montserrat-Black.ttf')
LATIN_SCALE = float(os.environ.get('VIRAL_LATIN_SCALE', '0.86'))


def run(*a):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', *a], check=True)


def font(size):
    return ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)


def _runs(text):
    """[[part, is_latin]]: English letters and digits in the Latin font, everything else in the Devanagari one."""
    out = []
    for ch in text:
        lat = (ch.isascii() and ch.isalnum()) if ch.isalnum() else (out[-1][1] if out else False)
        if out and out[-1][1] == lat:
            out[-1][0] += ch
        else:
            out.append([ch, lat])
    return out


def _fonts(size):
    return font(size), ImageFont.truetype(LATIN, max(8, int(size * LATIN_SCALE)), layout_engine=ImageFont.Layout.RAQM)


def width(d, text, size):
    fs = _fonts(size)
    return sum(d.textlength(t, font=fs[lat]) for t, lat in _runs(text))


def draw(d, x, base, text, size, fill):
    """Text on one baseline, run by run."""
    fs = _fonts(size)
    for t, lat in _runs(text):
        d.text((x, base), t, font=fs[lat], fill=fill, anchor='ls')
        x += d.textlength(t, font=fs[lat])


def ink_top(d, text, size):
    fs = _fonts(size)
    return min(d.textbbox((0, 0), t, font=fs[lat], anchor='ls')[1] for t, lat in _runs(text) if t.strip())


def fit(d, text, w, start):
    size = start
    while size > 40 and width(d, text, size) > w:
        size -= 2
    return size


def canvas(lines, out):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    rows = [(lines[0], (255, 255, 255), 169), (lines[1], (255, 255, 0), 343), (lines[2], (255, 255, 255), 1378), (lines[3], (255, 255, 0), 1591)]
    for pair in (rows[:2], rows[2:]):  # one size per pair: the two lines read as one block (his layout)
        size = min(fit(d, t, 1350, 190) for t, _, _ in pair)
        for text, col, top in pair:
            draw(d, (W - width(d, text, size)) / 2, top - ink_top(d, text, size), text, size, col)
    im.paste((0, 0, 0), (0, MY, W, MY + MH))
    im.save(out)


def _wrap(d, words, size, limit):
    lines, cur = [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if cur and width(d, t, size) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def card(img, lines, out):
    """His hook card: the picture left, the SPOKEN teaser line itself right, in big white type filling the panel
    (measured: 5 lines, e.g. "Youtuber शिवानी कुमारी पर मार्विन आची ने लगाया ऐसा आरोप कि फूट फूट कर रोने लगी शिवानी")."""
    im = Image.open(img).convert('RGB')
    s = max(MH / im.height, 720 / im.width) * 1.02
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1))
    x, y = (im.width - 720) // 2, (im.height - MH) // 2
    c = Image.new('RGB', (W, MH), (22, 22, 22))
    c.paste(im.crop((x, y, x + 720, y + MH)), (0, 0))
    d = ImageDraw.Draw(c)
    words = ' '.join(lines).split()
    size = 104
    while size > 56 and len(_wrap(d, words, size, 650)) > 6:  # as large as fits in 6 lines
        size -= 4
    rows, ascent = _wrap(d, words, size, 650), font(size).getmetrics()[0]
    step = int(size * 1.3)
    top = (MH - len(rows) * step) // 2
    for k, t in enumerate(rows):
        draw(d, 720 + (720 - width(d, t, size)) / 2, top + k * step + ascent, t, size, (255, 255, 255))
    c.save(out)
    return out


BLUR = ('split[a][b];[a]scale=288:160:force_original_aspect_ratio=increase,crop=288:160,boxblur=6:3,scale=1440:800,'
        'eq=brightness=-0.08[bm];'  # blurred at 1/5 size: same look, a fraction of the CPU
        '[b]scale=-2:800,setsar=1[fg];[bm][fg]overlay=(W-w)/2:0[m]')
FILL = 'scale=1440:800:force_original_aspect_ratio=increase,crop=1440:800,setsar=1[m]'
PARALLEL = int(os.environ.get('VIRAL_PARALLEL', '1'))
X264 = ['-c:v', 'libx264', '-crf', os.environ.get('VIRAL_CRF', '20'), '-preset', os.environ.get('VIRAL_PRESET', 'veryfast'), '-pix_fmt', 'yuv420p', '-r', '30', '-an']


def _size(src):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', src],
                       capture_output=True, text=True).stdout.strip().split(',')
    return int(r[0]), int(r[1])


def window(src, start, dur, out, still, canvas_png):
    """One shot drawn straight into the FULL frame (canvas + 16:9 window) and encoded once; the shots are then joined
    without re-encoding (MEASURED 10-31: encoding each shot at 1440x800 and the whole video again at 1440x1920 took
    391 s on Cloud Run for a 225 s video, over the free tier at 5 a day). A card or a wide picture fills the window; a
    tall photo or a phone video (most viral posts) shows whole over a blurred copy of itself."""
    w, h = Image.open(src).size if still else _size(src)
    head = '[0:v]' + ('loop=loop=-1:size=1:start=0,setpts=N/30/TB,' if still else 'fps=30,')  # a still decoded once
    hold = '' if still else ',tpad=stop_mode=clone:stop_duration=%.3f' % dur  # a clip shorter than its slot holds its last frame
    graph = (head + (FILL if w / h >= 1.4 else BLUR) +
             ';[1:v]format=yuv420p,loop=loop=-1:size=1:start=0,setpts=N/30/TB[c];'
             '[c][m]overlay=0:%d:shortest=1%s,trim=duration=%.3f,setpts=PTS-STARTPTS[v]' % (MY, hold, dur))
    run(*([] if still else ['-ss', '%.3f' % start]), '-i', src, '-i', canvas_png, '-filter_complex', graph, '-map', '[v]',
        '-t', '%.3f' % dur, *X264, out)
    return out


def peak(path):
    r = subprocess.run(['ffmpeg', '-i', path, '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.search(r'max_volume: (-?[\d.]+) dB', r).group(1))


def render(work, spec):
    j = lambda f: os.path.join(work, f)
    canvas(spec['headline'], j('canvas.png'))
    cards = [card(j(c['img']), c['lines'], j('card%02d.png' % k)) for k, c in enumerate(spec['cards'])]
    jobs, b = [], spec.get('burst')
    for k, s in enumerate(spec['shots']):
        src = cards[s['card']] if 'card' in s else j(s['src'])
        jobs.append((src, s.get('start', 0), s['dur'], j('seg%03d.mp4' % len(jobs)), 'card' in s or s.get('still'), j('canvas.png')))
        if b and k == b['after_shot']:
            jobs.append((j(b['src']), b['start'], b['dur'], j('seg%03d.mp4' % len(jobs)), False, j('canvas.png')))
    # shots one at a time by default: 3 at once measured no faster (72.1 vs 71.7 s), x264 already fills 4 vCPUs
    with ThreadPoolExecutor(PARALLEL) as ex:
        segs = list(ex.map(lambda a: window(*a), jobs))
    open(j('segs.txt'), 'w').write(''.join("file '%s'\n" % s for s in segs))
    # sound: narration (with the burst of the clip's own sound spliced in), one whoosh-boom per teaser line at voice level
    narr = j('voice.wav')
    if b:
        split = b['at']
        run('-i', narr, '-t', '%.3f' % split, '-ar', '48000', '-ac', '2', j('vA.wav'))
        run('-ss', '%.3f' % split, '-i', narr, '-ar', '48000', '-ac', '2', j('vB.wav'))
        run('-ss', '%.3f' % b['start'], '-t', '%.3f' % b['dur'], '-i', j(b['src']), '-vn', '-af',
            'loudnorm=I=-16:TP=-2,afade=t=in:d=0.08,afade=t=out:st=%.2f:d=0.15' % (b['dur'] - 0.15), '-ar', '48000', '-ac', '2', j('burst.wav'))
        run('-i', j('vA.wav'), '-i', j('burst.wav'), '-i', j('vB.wav'), '-filter_complex', '[0][1][2]concat=n=3:v=0:a=1[a]', '-map', '[a]', j('narr.wav'))
        narr = j('narr.wav')
    gain = peak(narr) - peak(j('sfx.wav')) - 1.0  # measured: his hits peak at about the voice's own level
    ins, chains = ['-i', narr], []
    for k, t in enumerate(spec['hits']):
        ins += ['-i', j('sfx.wav')]
        ms = int(max(0, t - 0.05) * 1000)
        chains.append('[%d:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=%.1fdB,adelay=%d|%d[h%d]' % (k + 1, gain, ms, ms, k))
    graph = ';'.join(chains) + (';' if chains else '') + '[0:a]' + ''.join('[h%d]' % k for k in range(len(spec['hits']))) + \
        'amix=inputs=%d:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5[a]' % (len(spec['hits']) + 1)
    run(*ins, '-filter_complex', graph, '-map', '[a]', '-ar', '48000', j('audio.wav'))
    out = j('reel.mp4')
    run('-f', 'concat', '-safe', '0', '-i', j('segs.txt'), '-i', j('audio.wav'), '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out)
    return out


if __name__ == '__main__':  # local test: python viral_render.py <work dir with job.json>
    import sys
    print(render(sys.argv[1], json.load(open(os.path.join(sys.argv[1], 'job.json')))))
