"""No duplicate posts: the same story, X post, caption or (within 12 h) main subject is never posted twice."""
import hashlib
import re
from datetime import datetime, timedelta

from .schedule import history, now

STOP = set('a an the and or but of to in on at for with by from as is are was were be been it its this that these those '
           'after before over under into about than then new says say said will would could can may might just more most '
           'his her their they he she we you i who what why how when where us u.s. vs his him them our not no yes up out '
           'off amid report reports reported update live news today'.split())
WINDOW = timedelta(hours=72)
SUBJECT_WINDOW = timedelta(hours=12)
LIMIT = 0.4  # word-set overlap (Jaccard) at or above this counts as the same story


def words(text):
    return {w for w in re.findall(r"[a-z0-9$']+", (text or '').lower()) if w not in STOP and len(w) > 2}


def _overlap(a, b):
    return len(a & b) / max(1, len(a | b))


def recent(window=WINDOW):
    cutoff = now() - window
    return [h for h in history() if datetime.fromisoformat(h['time']) >= cutoff]


def recent_headlines(n=60):
    return [h['headline'] for h in recent()[-n:]]


def story_seen(*texts):
    """True if this story (title / query / headline) overlaps a post from the last 72 h."""
    new = set().union(*(words(t) for t in texts))
    for h in recent():
        old = words(h.get('title')) | words(h.get('query')) | words(h.get('headline'))
        if _overlap(new, old) >= LIMIT or _overlap(words(texts[-1]), words(h.get('headline'))) >= LIMIT:
            return h['headline']
    return None


def tweet_used(tid):
    return any(h.get('tweet_id') == tid for h in history())


def subject_recent(subject):
    if not subject:
        return False
    return any((h.get('subject') or '').lower() == subject.lower() for h in recent(SUBJECT_WINDOW))


def caption_hash(caption):
    return hashlib.sha1(re.sub(r'\W+', '', caption.lower()).encode()).hexdigest()[:16]


def caption_used(caption):
    c = caption_hash(caption)
    return any(h.get('caption_hash') == c for h in history())


def named_in(subject, headline):
    """True if the headline names the subject (e.g. 'Elon Musk' -> 'Musk', 'Meta Platforms' -> 'Meta')."""
    name = re.sub(r'\s*\(.*?\)', '', subject or '').lower()
    head = (headline or '').lower()
    return any(len(w) > 2 and re.search(r'\b' + re.escape(w) + r'\b', head) for w in name.split()
               if w not in {'the', 'of', 'and', 'inc', 'inc.', 'corporation', 'company', 'platforms'})
