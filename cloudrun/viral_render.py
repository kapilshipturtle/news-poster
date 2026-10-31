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
from PIL import Image, ImageDraw, ImageFont

W, H, MY, MH = 1440, 1920, 565, 800
BG = (122, 22, 22)
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'fonts', 'NotoSansDevanagari-ExtraBold.ttf')


def run(*a):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', *a], check=True)


def font(size):
    return ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)


def fit(d, text, width, start):
    size = start
    while size > 40 and d.textlength(text, font=font(size)) > width:
        size -= 2
    return size


def canvas(lines, out):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    rows = [(lines[0], (255, 255, 255), 169), (lines[1], (255, 255, 0), 343), (lines[2], (255, 255, 255), 1378), (lines[3], (255, 255, 0), 1591)]
    for pair in (rows[:2], rows[2:]):  # one size per pair: the two lines read as one block (his layout)
        f = font(min(fit(d, t, 1350, 190) for t, _, _ in pair))
        for text, col, top in pair:
            x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
            d.text(((W - (x1 - x0)) / 2 - x0, top - y0), text, font=f, fill=col)
    im.paste((0, 0, 0), (0, MY, W, MY + MH))
    im.save(out)


def _wrap(d, words, size, width):
    f, lines, cur = font(size), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if cur and d.textlength(t, font=f) > width:
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
    rows, f = _wrap(d, words, size, 650), font(size)
    step = int(size * 1.3)
    top = (MH - len(rows) * step) // 2
    for k, t in enumerate(rows):
        d.text((720 + (720 - d.textlength(t, font=f)) / 2, top + k * step), t, font=f, fill=(255, 255, 255))
    c.save(out)
    return out


BLUR = ('[0:v]split[a][b];[a]scale=1440:800:force_original_aspect_ratio=increase,crop=1440:800,boxblur=30:3,eq=brightness=-0.08[bg];'
        '[b]scale=-2:800[fg];[bg][fg]overlay=(W-w)/2:0,')
FILL = 'scale=1440:800:force_original_aspect_ratio=increase,crop=1440:800,'


def _size(src):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', src],
                       capture_output=True, text=True).stdout.strip().split(',')
    return int(r[0]), int(r[1])


def window(src, start, dur, out, still):
    """A card or a wide picture fills the 16:9 window; a tall photo or a phone video (most viral posts) shows whole over
    a blurred copy of itself."""
    w, h = Image.open(src).size if still else _size(src)
    look = FILL if w / h >= 1.4 else BLUR
    vf = look + 'setsar=1,fps=30,format=yuv420p'
    if still:
        run('-loop', '1', '-t', '%.3f' % dur, '-i', src, '-filter_complex' if look == BLUR else '-vf', vf,
            '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast', '-an', out)
    else:  # a clip shorter than its slot holds its last frame
        run('-ss', '%.3f' % start, '-i', src, '-filter_complex' if look == BLUR else '-vf',
            vf + ',tpad=stop_mode=clone:stop_duration=%.3f' % dur, '-t', '%.3f' % dur,
            '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast', '-an', out)
    return out


def peak(path):
    r = subprocess.run(['ffmpeg', '-i', path, '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.search(r'max_volume: (-?[\d.]+) dB', r).group(1))


def render(work, spec):
    j = lambda f: os.path.join(work, f)
    canvas(spec['headline'], j('canvas.png'))
    cards = [card(j(c['img']), c['lines'], j('card%02d.png' % k)) for k, c in enumerate(spec['cards'])]
    segs, b = [], spec.get('burst')
    for k, s in enumerate(spec['shots']):
        src = cards[s['card']] if 'card' in s else j(s['src'])
        segs.append(window(src, s.get('start', 0), s['dur'], j('seg%03d.mp4' % len(segs)), 'card' in s or s.get('still')))
        if b and k == b['after_shot']:
            segs.append(window(j(b['src']), b['start'], b['dur'], j('seg%03d.mp4' % len(segs)), False))
    open(j('segs.txt'), 'w').write(''.join("file '%s'\n" % s for s in segs))
    run('-f', 'concat', '-safe', '0', '-i', j('segs.txt'), '-c', 'copy', j('media.mp4'))
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
    run('-loop', '1', '-i', j('canvas.png'), '-i', j('media.mp4'), '-i', j('audio.wav'), '-filter_complex',
        '[0:v][1:v]overlay=0:%d:shortest=1,format=yuv420p[v]' % MY, '-map', '[v]', '-map', '2:a', '-r', '30', '-c:v', 'libx264', '-crf', '20',
        '-preset', 'medium', '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out)
    return out


if __name__ == '__main__':  # local test: python viral_render.py <work dir with job.json>
    import sys
    print(render(sys.argv[1], json.load(open(os.path.join(sys.argv[1], 'job.json')))))
