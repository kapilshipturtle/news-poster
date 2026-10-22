"""Gemini (free API): pick the story, choose the X post, write headline + caption."""
import json
import os
import re
import time
import urllib.error
import urllib.request

# Pinned, non-Preview models only (Gemini API terms forbid production use of Preview models).
# MEASURED 2026-10-22: the free tier gives Gemini 3.8 Flash and 3.5 Flash only 20 requests per day each, so the
# writing (captions, Reel scripts) gets them; the many background calls (picking, duplicate checks, frame checks,
# edit plans) use Gemma 4 31B on the same free key (works with JSON and images, ~60-80 s per call).
CHAINS = {
    # 'groq:' models run on GroqCloud's free plan (MEASURED: 1,000 requests/day per model, 8,000 tokens/min;
    # gpt-oss-120b wrote a 135-word Reel script in 7.5 s, Qwen 3.8 27B read an image in 1.5 s)
    'best': os.environ.get('AI_BEST', 'gemini-3.8-flash,gemini-3.5-flash,groq:openai/gpt-oss-120b,gemma-4-31b-it').split(','),
    'bulk': os.environ.get('AI_BULK', 'groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b,gemma-4-31b-it').split(','),
    'vision': os.environ.get('AI_VISION', 'groq:qwen/qwen3.8-27b,gemma-4-31b-it').split(','),
}
TIMEOUT = int(os.environ.get('GEMINI_TIMEOUT', '60'))  # seconds for Gemini Flash; Gemma gets 3x


def _quota_path():
    from .net import STATE
    return os.path.join(STATE, 'ai_quota.json')


def _exhausted():
    try:
        d = json.load(open(_quota_path()))
    except (OSError, ValueError):
        return {}
    return {m: t for m, t in d.items() if t > time.time()}


def _mark_exhausted(model, seconds):
    d = _exhausted()
    d[model] = time.time() + seconds
    try:
        json.dump(d, open(_quota_path(), 'w'))
    except OSError:
        pass


def _json_from(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(json)?|```$', '', text, flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        for a, b in (('{', '}'), ('[', ']')):
            i, j = text.find(a), text.rfind(b)
            if i != -1 and j > i:
                try:
                    return json.loads(text[i:j + 1])
                except ValueError:
                    continue
        raise ValueError('no JSON in the reply')


def _call(chain, parts, temperature):
    """Try each model of the chain: a busy/hung model gets one retry, a model whose DAILY quota is used up is skipped
    until Google's stated reset (no waiting on it again)."""
    err = None
    skip = _exhausted()
    for model in CHAINS[chain]:
        model = model.strip()
        if model in skip:
            continue
        if model.startswith('groq:'):
            if not os.environ.get('GROQ_API_KEY'):
                continue
            try:
                return _groq(model[5:], parts, temperature)
            except Exception as e:  # noqa: BLE001 - the next model in the chain takes over
                err = e
                continue
        gemma = model.startswith('gemma')
        cfg = {'temperature': temperature}
        if not gemma:
            cfg['responseMimeType'] = 'application/json'  # Gemma via this API answers in text; JSON is parsed out
        body = json.dumps({'contents': [{'parts': parts}], 'generationConfig': cfg}).encode()
        url = 'https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}'.format(model, os.environ['GEMINI_API_KEY'])
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=TIMEOUT * (3 if gemma else 1) * (2 if len(parts) > 1 else 1)) as r:
                    d = json.loads(r.read())
                texts = [p_.get('text', '') for p_ in d['candidates'][0]['content']['parts'] if not p_.get('thought')]
                return _json_from(texts[-1])
            except urllib.error.HTTPError as e:
                err = e
                body_txt = e.read().decode('utf-8', 'ignore')
                if e.code == 429 and 'PerDay' in body_txt:
                    m = re.search(r'"retryDelay":\s*"(\d+)', body_txt)
                    _mark_exhausted(model, int(m.group(1)) if m else 3600)
                    break  # daily quota used up: next model at once
                if e.code in (429, 500, 503) and attempt < 1:
                    time.sleep(10)
                    continue
                break
            except (TimeoutError, OSError, ValueError, KeyError, IndexError) as e:
                err = e
                if attempt < 1:
                    time.sleep(5)
                    continue
                break
    raise err or RuntimeError('every model is out of free quota today')


def _groq(model, parts, temperature):
    """GroqCloud (OpenAI-compatible). Waits out a per-minute limit (Groq says how long); a used-up DAILY quota marks
    the model until its reset, like Gemini's."""
    content = parts[0]['text'] if len(parts) == 1 else [
        {'type': 'text', 'text': p_['text']} if 'text' in p_ else
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + p_['inline_data']['data']}} for p_ in parts]
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': content}], 'temperature': temperature,
                       'response_format': {'type': 'json_object'}}).encode()
    headers = {'Authorization': 'Bearer ' + os.environ['GROQ_API_KEY'], 'Content-Type': 'application/json',
               'User-Agent': 'news-poster'}
    for attempt in range(3):
        try:
            req = urllib.request.Request('https://api.groq.com/openai/v1/chat/completions', data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                return _json_from(json.loads(r.read())['choices'][0]['message']['content'])
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'ignore')
            wait = float(e.headers.get('retry-after') or 0)
            if e.code == 429 and ('per day' in txt.lower() or 'RPD' in txt or wait > 300):
                _mark_exhausted('groq:' + model, max(wait, 3600))
                raise
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(min(max(wait, 3), 65))  # per-minute token/request limit: wait as told
                continue
            raise


def gemini(prompt, temperature=0.7, chain='best'):
    return _call(chain, [{'text': prompt}], temperature)


PICK = """You run a US Facebook news page. Choose the stories most likely to get Americans reacting, commenting and sharing in the next few hours.
Each candidate shows how many US outlets are carrying it right now (more outlets = more trending).
Prefer: big national stories, celebrities, sports moments, money/tech everyone uses, surprising or hopeful turns, things people argue about.
Avoid: stories already covered (RECENTLY POSTED list), local-only items, paywalled finance jargon, graphic tragedies with no new angle, non-US stories Americans won't care about.
Mix topics: do not pick the same kind of story as the last 2 posts unless it is clearly the biggest story.

CANDIDATES (number. outlets carrying it | title):
{cands}

RECENTLY POSTED (do NOT repeat these stories, even reworded):
{recent}

Return JSON: {{"picks": [{{"n": <candidate number>, "query": "<4-8 word news search query for this exact story>", "tag": "<ONE label: BREAKING, POLITICS, NFL, NBA, MLB, SPORTS, ENTERTAINMENT, BUSINESS, TECH, HEALTH, WORLD, CRIME, WEATHER, SCIENCE>", "subject": "<exact English Wikipedia article title of the ONE person/organisation the story is about (the one a headline would name), or null if that person/organisation has no Wikipedia article. Never a related figure.>"}}]}}
Give 4 picks, best first."""

WRITE = """You write posts for a US Facebook news page that people follow because its posts are quick, human and make them feel something.
The page name is not part of any story; never mention it. The story is whatever the SOURCES below report.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names or outcomes. If sources disagree, use only what they agree on.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

X POSTS found embedded in the coverage (index | author | likes | text):
{tweets}

Return JSON: {{"tweet_index": <index of the ONE X post that is directly about this exact story (a statement by someone involved, the original report, the key photo); null if none fits>, "headline": str, "fact": str, "caption": str}}
headline: max 10 words, punchy, factual, plain words, sentence case, no emoji. It goes on the image in big bold letters and must match the story exactly (no clickbait).
fact: the single most striking concrete detail (a number, a quote fragment, a consequence) in max 12 words, shown under the headline.
caption:
- Line 1 (max 12 words): the emotional core. Surprise, stakes, a human detail, relief, outrage that the facts justify. Write it so a scroller stops; do not open with a name and a verb like a wire report. The emotion must come from a REAL detail in the sources: never add imagined scenes or sensory details (no "pounding on doors" unless a source says so).
- Then 2-3 very short lines in your OWN words: what happened and why it matters to an ordinary American. Plain everyday words, no jargon (say "lung cancer", not "pulmonary adenocarcinoma"). No "according to", no outlet names, no credits.
- Last line: one specific question people will genuinely disagree about (a choice, a judgment, "would you..."), never a vague one like "how should officials balance...". NEVER engagement bait: no "comment YES", "type 1", "share if", "like if", "tag a friend", "who agrees", no voting by reaction.
- 35-80 words total. Max 1 emoji, no hashtags, no links, no em dashes. Never use: delve, tapestry, testament, game-changer, buckle up, in a world where, here's the thing, let that sink in, breaking news.
- Blank line between paragraphs.

SOURCES:
{sources}"""


def pick_stories(cands, recent):
    lines = '\n'.join('{}. {} ({}) | {}'.format(i, len(c['outlets']), ', '.join(c['outlets'][:4]), c['title'])
                      for i, c in enumerate(cands))
    rec = '\n'.join('- ' + r for r in recent) or '- (none yet)'
    out = gemini(PICK.format(cands=lines, recent=rec), temperature=0.4, chain='bulk')
    picks = []
    for p in (out if isinstance(out, list) else out.get('picks', [])):  # lighter models sometimes return the bare list
        if not isinstance(p, dict):
            continue
        n = p.get('n')
        if isinstance(n, int) and 0 <= n < len(cands) and p.get('query'):
            p['title'], p['cluster'] = cands[n]['title'], cands[n]
            picks.append(p)
    return picks


def write_post(page, sources, tweets, fix=None):
    t = '\n'.join('{} | @{} ({}) | {} likes | {}'.format(i, x['handle'], x['name'], x['likes'], x['text'][:400])
                  for i, x in enumerate(tweets)) or '(none)'
    src = '\n\n'.join('[{}] {}\n{}'.format(a['outlet'], a['title'], a['text']) for a in sources)
    prompt = WRITE.format(page=page, tweets=t, sources=src)
    if fix:
        prompt += '\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED FOR: {}. Fix exactly these problems.'.format('; '.join(fix))
    out = gemini(prompt)
    if isinstance(out, list):  # a one-item list instead of the object
        out = out[0] if out and isinstance(out[0], dict) else {'skip': 'model returned no post'}
    return out


SAME = """Is the NEW post about the same news event as any RECENT post? Same event = same incident/announcement/decision,
even if reworded or a small update (e.g. "Russia frees Hubbard" and "Hubbard's sister thanks Trump" are the SAME event).
Different events about the same person are NOT the same (e.g. two different Musk announcements).

NEW: {new}

RECENT:
{recent}

Return JSON: {{"same_as": <number of the matching RECENT post, or null>}}"""


def same_event(new, recent):
    """Meaning-level duplicate check; returns the matching recent headline or None."""
    if not recent:
        return None
    out = gemini(SAME.format(new=new, recent='\n'.join('{}. {}'.format(i, r) for i, r in enumerate(recent))), temperature=0, chain='bulk')
    n = out.get('same_as') if isinstance(out, dict) else None
    return recent[n] if isinstance(n, int) and 0 <= n < len(recent) else None


def gemini_vision(prompt, jpegs, temperature=0.2, chain='vision'):
    """Gemini/Gemma with images (contact sheets), through the same chain rules as gemini()."""
    import base64
    parts = [{'text': prompt}] + [{'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(open(j, 'rb').read()).decode()}}
                                  for j in jpegs]
    return _call(chain, parts, temperature)
