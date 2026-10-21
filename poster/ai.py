"""Gemini (free API): pick the story, choose the X post, write headline + caption."""
import json
import os
import time
import urllib.error
import urllib.request

# pinned, non-Preview models only: Gemini API terms forbid production use of Preview models
MODELS = os.environ.get('GEMINI_MODELS', 'gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite').split(',')


def gemini(prompt, temperature=0.7):
    body = json.dumps({'contents': [{'parts': [{'text': prompt}]}],
                       'generationConfig': {'responseMimeType': 'application/json', 'temperature': temperature}}).encode()
    err = None
    for model in MODELS:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}'.format(
            model.strip(), os.environ['GEMINI_API_KEY'])
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=120) as r:
                    d = json.loads(r.read())
                return json.loads(d['candidates'][0]['content']['parts'][0]['text'])
            except urllib.error.HTTPError as e:
                err = e
                if e.code in (429, 500, 503) and attempt < 2:  # free-tier rate limit / overload: back off
                    time.sleep(20 * (attempt + 1))
                    continue
                break  # try the next model
    raise err


PICK = """You run a US Facebook news page. Choose the stories most likely to get Americans reacting, commenting and sharing in the next few hours.
Each candidate shows how many US outlets are carrying it right now (more outlets = more trending).
Prefer: big national stories, celebrities, sports moments, money/tech everyone uses, surprising or hopeful turns, things people argue about.
Avoid: stories already covered (RECENTLY POSTED list), local-only items, paywalled finance jargon, graphic tragedies with no new angle, non-US stories Americans won't care about.
Mix topics: do not pick the same kind of story as the last 2 posts unless it is clearly the biggest story.

CANDIDATES (number. outlets carrying it | title):
{cands}

RECENTLY POSTED (do NOT repeat these stories, even reworded):
{recent}

Return JSON: {{"picks": [{{"n": <candidate number>, "query": "<4-8 word news search query for this exact story>", "tag": "<ONE label: BREAKING, POLITICS, NFL, NBA, MLB, SPORTS, ENTERTAINMENT, BUSINESS, TECH, HEALTH, WORLD, CRIME, WEATHER, SCIENCE>", "subject": "<exact English Wikipedia article title of the main person/thing, or null>"}}]}}
Give 4 picks, best first."""

WRITE = """You write posts for "{page}", a US Facebook news page. Write like a sharp human editor, not an AI.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names or outcomes. If sources disagree, use only what they agree on.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

X POSTS found embedded in the coverage (index | author | likes | text):
{tweets}

Return JSON: {{"tweet_index": <index of the ONE X post that is directly about this exact story and adds something (a statement, the original report, the key photo); null if none fits>, "headline": str, "caption": str}}
headline: max 10 words, punchy, factual, plain words, sentence case, no emoji. It goes on the image in big bold letters. It must match the story exactly (no clickbait).
caption:
- Line 1: a hook that makes a scroller stop, max 15 words. Lead with surprise, curiosity, stakes or a human detail rather than doom (negative framing measurably gets fewer likes and comments), but stay factual.
- Then 2-4 short lines of context in your OWN words. This is what makes the post original, not a repost.
- {credit}
- Last line: one genuine question that invites opinions. NEVER engagement bait: no "comment YES", "type 1", "share if", "like if", "tag a friend", "who agrees", no voting by reaction.
- Max 2 emoji total, max 2 hashtags (only if natural). No links. No em dashes. Never use: delve, tapestry, testament, game-changer, buckle up, in a world where, here's the thing, let that sink in, breaking news.
- 40-110 words total. Blank line between paragraphs.

SOURCES:
{sources}"""

CREDIT_TWEET = 'If you choose an X post, credit it naturally, e.g. "(via @handle on X)", and comment on it rather than repeating it. If not, name the main outlet that reported it.'
CREDIT_TEXT = 'Name the main outlet that reported it, e.g. "according to NBC News". The caption must carry the whole story on its own.'


def pick_stories(cands, recent):
    lines = '\n'.join('{}. {} ({}) | {}'.format(i, len(c['outlets']), ', '.join(c['outlets'][:4]), c['title'])
                      for i, c in enumerate(cands))
    rec = '\n'.join('- ' + r for r in recent) or '- (none yet)'
    out = gemini(PICK.format(cands=lines, recent=rec), temperature=0.4)
    picks = []
    for p in (out if isinstance(out, list) else out.get('picks', [])):  # lighter models sometimes return the bare list
        if not isinstance(p, dict):
            continue
        n = p.get('n')
        if isinstance(n, int) and 0 <= n < len(cands) and p.get('query'):
            p['title'], p['cluster'] = cands[n]['title'], cands[n]
            picks.append(p)
    return picks


def write_post(page, sources, tweets, allow_tweet=True, fix=None):
    t = '\n'.join('{} | @{} ({}) | {} likes | {}'.format(i, x['handle'], x['name'], x['likes'], x['text'][:400])
                  for i, x in enumerate(tweets)) or '(none)'
    src = '\n\n'.join('[{}] {}\n{}'.format(a['outlet'], a['title'], a['text']) for a in sources)
    prompt = WRITE.format(page=page, tweets=t, sources=src, credit=CREDIT_TWEET if allow_tweet and tweets else CREDIT_TEXT)
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
    out = gemini(SAME.format(new=new, recent='\n'.join('{}. {}'.format(i, r) for i, r in enumerate(recent))), temperature=0)
    n = out.get('same_as') if isinstance(out, dict) else None
    return recent[n] if isinstance(n, int) and 0 <= n < len(recent) else None
