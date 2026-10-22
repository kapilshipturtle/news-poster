"""Post images (1080x1350) drawn as HTML and captured with headless Chrome."""
import html
import os
import subprocess

from .net import get

CHROME = os.environ.get('CHROME', 'google-chrome')

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'fonts')
# fonts ship with the code: fetching Google Fonts on every render stalled Chrome past its time limit on the small VM
CSS = '''@font-face{font-family:Anton;src:url(file://FONTS/anton-400.woff2)}
@font-face{font-family:Inter;src:url(file://FONTS/inter.woff2);font-weight:100 900}
*{margin:0;box-sizing:border-box}html,body{width:1080px;height:1350px;background:#fff;font-family:Inter,sans-serif;overflow:hidden}
body{display:flex;flex-direction:column}
.wrap{width:1080px;height:1350px;display:flex;flex-direction:column;padding:56px 64px 40px}
.mid{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center}
.tag{align-self:flex-start;background:#e11d2e;color:#fff;font:800 26px Inter;letter-spacing:2px;padding:10px 18px;border-radius:6px}
.hl{font-family:Anton,Impact,sans-serif;font-size:84px;line-height:1.02;color:#0b0b0b;text-transform:uppercase;margin:22px 0 30px}
.emb{flex:1;min-height:0;display:flex;align-items:flex-start;justify-content:center}
.emb img{max-width:100%;max-height:100%;object-fit:contain;border-radius:14px;box-shadow:0 10px 40px rgba(0,0,0,.10)}
.brand{margin-top:26px;display:flex;justify-content:space-between;font:800 26px Inter;color:#0b0b0b;letter-spacing:1px}.brand span{color:#e11d2e}
.photo{flex:none;width:1080px;height:760px;object-fit:cover;object-position:center 22%;display:block}
.card{flex:1;padding:40px 64px 40px;display:flex;flex-direction:column}.card .hl{font-size:92px;margin:22px 0 0;flex:1}
.typo .hl{font-size:124px;border-left:16px solid #e11d2e;padding-left:36px}
.fact{font:600 40px/1.3 Inter;color:#333;margin-top:34px;padding-left:52px}'''.replace('FONTS', FONTS)


def _save(url, path):
    if not os.path.exists(path):
        open(path, 'wb').write(get(url, raw=True, timeout=30))
    return 'file://' + path


def render(kind, headline, tag, page, work, embed_png=None, photo_url=None, fact=None):
    """kind: 'tweet' (official X embed screenshot) | 'photo' (free-licensed photo) | 'typo'. Returns the PNG path."""
    os.makedirs(work, exist_ok=True)
    brand = '<div class="brand"><div><span>●</span> {}</div><div>Follow for more</div></div>'.format(html.escape(page))
    tag_html = '<div class="tag">{}</div>'.format(html.escape(tag))
    hl = '<div class="hl">{}</div>'.format(html.escape(headline))
    cls = ''
    if kind == 'tweet':
        body = '<div class="wrap">{}{}<div class="emb"><img src="file://{}"></div>{}</div>'.format(tag_html, hl, embed_png, brand)
    elif kind == 'photo':
        body = '<img class="photo" src="{}"><div class="card">{}{}{}</div>'.format(
            _save(photo_url, work + '/subject.jpg'), tag_html, hl, brand)
    else:
        fact_html = '<div class="fact">{}</div>'.format(html.escape(fact)) if fact else ''
        body, cls = '<div class="wrap"><div class="mid">{}{}{}</div>{}</div>'.format(tag_html, hl, fact_html, brand), 'typo'
    page_html = work + '/post.html'
    png = work + '/image.png'
    open(page_html, 'w').write('<!doctype html><html><head><meta charset="utf-8"><style>{}</style></head>'
                               '<body class="{}">{}</body></html>'.format(CSS, cls, body))
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                    '--force-device-scale-factor=1', '--window-size=1080,1350', '--virtual-time-budget=10000',
                    '--screenshot=' + png, 'file://' + page_html], check=True, capture_output=True, timeout=300)
    return png
