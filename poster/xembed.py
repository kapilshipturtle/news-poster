"""X posts through X's official embed (platform.twitter.com) — the same widget news sites use — captured unaltered.
Gives the screenshot plus what the widget shows: author, text, date, likes."""
import os
import re
from datetime import datetime, timedelta

MAX_AGE = timedelta(days=4)
MAX_TALL = 1.30  # height / width; taller embeds (quote posts, threads) would be unreadable in the post


def _likes(s):
    m = re.match(r'([\d.,]+)\s*([KM]?)$', s.strip())
    if not m:
        return None
    n = float(m.group(1).replace(',', ''))
    return int(n * {'K': 1e3, 'M': 1e6}.get(m.group(2), 1))


def capture(tweet_ids, work, max_n=4, min_likes=500, max_age=MAX_AGE):
    """Screenshot up to max_n candidate posts; return the usable ones (fresh, popular, not too tall)."""
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        # Chrome 132+ removed the old headless mode that Playwright 1.48 asks for; request the new one explicitly
        browser = p.chromium.launch(channel=os.environ.get('PW_CHANNEL', 'chrome'), headless=False, args=['--headless=new'])
        ctx = browser.new_context(viewport={'width': 550, 'height': 1600}, device_scale_factor=2,
                                  timezone_id='America/New_York', locale='en-US')
        for tid in tweet_ids[:max_n]:
            page = ctx.new_page()
            try:
                page.goto('https://platform.twitter.com/embed/Tweet.html?id={}&theme=light&dnt=true&lang=en&hideThread=true'
                          .format(tid), wait_until='networkidle', timeout=45000)
                art = page.locator('article').first
                art.wait_for(timeout=20000)
                page.wait_for_timeout(1200)
                page.evaluate('Promise.all([...document.images].map(i => i.complete ? 1 : new Promise(r => {i.onload = i.onerror = r})))')
                text = art.inner_text()
                box = art.bounding_box()
                png = os.path.join(work, 'x_{}.png'.format(tid))
                art.screenshot(path=png)
            except Exception:
                page.close()
                continue
            page.close()
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            date = next((l for l in lines if re.search(r'\d{1,2}:\d{2} [AP]M · \w{3} \d{1,2}, \d{4}', l)), None)
            likes = next((_likes(lines[i - 1]) for i, l in enumerate(lines) if l == 'Reply' and i), None)
            try:
                posted = datetime.strptime(date.split(' · ')[1], '%b %d, %Y') if date else None
            except ValueError:
                posted = None
            handle = next((l for l in lines if l.startswith('@')), '')
            body = text
            if date:
                body = text.split(date)[0]
            item = {'id': tid, 'png': png, 'handle': handle.lstrip('@'), 'name': lines[0] if lines else '',
                    'text': re.sub(r'^[·\s]*Follow\s*', '', re.sub(r'\s+', ' ', body.replace(lines[0], '', 1).replace(handle, '', 1)).strip())[:500],
                    'likes': likes or 0, 'date': date, 'tall': box['height'] / box['width'] if box else 9}
            fresh = max_age is None or (posted is not None and datetime.now() - posted <= max_age)
            if fresh and item['likes'] >= min_likes and item['tall'] <= MAX_TALL:
                out.append(item)
        browser.close()
    return sorted(out, key=lambda t: -t['likes'])
