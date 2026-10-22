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

Return JSON: {{"picks": [{{"n": <candidate number>, "query": "<4-8 word news search query for this exact story>", "tag": "<ONE label: BREAKING, POLITICS, NFL, NBA, MLB, SPORTS, ENTERTAINMENT, BUSINESS, TECH, HEALTH, WORLD, CRIME, WEATHER, SCIENCE>", "subject": "<exact English Wikipedia article title of the ONE person/organisation the story is about (the one a headline would name), or null if that person/organisation has no Wikipedia article. Never a related figure.>"}}]}}
Give 4 picks, best first."""

WRITE = """You write posts for "{page}", a US Facebook news page that people follow because its posts are quick, human and make them feel something.
Use ONLY facts stated in the SOURCES. Never invent numbers, quotes, names or outcomes. If sources disagree, use only what they agree on.
If the sources are too thin or contradict each other on the core fact, return {{"skip": "<reason>"}}.

X POSTS found embedded in the coverage (index | author | likes | text):
{tweets}

Return JSON: {{"tweet_index": <index of the ONE X post that is directly about this exact story (a statement by someone involved, the original report, the key photo); null if none fits>, "headline": str, "fact": str, "caption": str}}
headline: max 10 words, punchy, factual, plain words, sentence case, no emoji. It goes on the image in big bold letters and must match the story exactly (no clickbait).
fact: the single most striking concrete detail (a number, a quote fragment, a consequence) in max 12 words, shown under the headline.
caption:
- Line 1 (max 12 words): the emotional core. Surprise, stakes, a human detail, relief, outrage that the facts justify. Write it so a scroller stops; do not open with a name and a verb like a wire report.
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
    out = gemini(SAME.format(new=new, recent='\n'.join('{}. {}'.format(i, r) for i, r in enumerate(recent))), temperature=0)
    n = out.get('same_as') if isinstance(out, dict) else None
    return recent[n] if isinstance(n, int) and 0 <= n < len(recent) else None
