"""Random daily posting times, posting history and the safety brake. All state lives in state/*.json (committed by the workflow)."""
import json
import os
import random
from datetime import datetime, timedelta, timezone

from .net import STATE

PER_DAY = int(os.environ.get('POSTS_PER_DAY', '24'))
MIN_GAP = timedelta(minutes=int(os.environ.get('MIN_GAP_MIN', '20')))
STALE = timedelta(minutes=60)


def _load(name, default):
    p = os.path.join(STATE, name)
    return json.load(open(p)) if os.path.exists(p) else default


def _save(name, data):
    os.makedirs(STATE, exist_ok=True)
    tmp = os.path.join(STATE, name + '.tmp')
    json.dump(data, open(tmp, 'w'), indent=1)
    os.replace(tmp, os.path.join(STATE, name))


def now():
    return datetime.now(timezone.utc)


def _slots_for(day):
    """PER_DAY random times inside the UTC day, at least MIN_GAP apart. Seeded by the day, so a lost
    schedule.json regenerates the very same times instead of a fresh set (which could double-post)."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rng = random.Random('{}-{}'.format(day.isoformat(), os.environ.get('FB_PAGE_ID', '')))
    while True:
        mins = sorted(rng.uniform(0, 24 * 60) for _ in range(PER_DAY))
        if all(b - a >= MIN_GAP.total_seconds() / 60 for a, b in zip(mins, mins[1:])):
            return [(start + timedelta(minutes=m)).isoformat() for m in mins]


def today_plan():
    plan = _load('schedule.json', {})
    day = now().date().isoformat()
    if plan.get('day') != day:
        plan = {'day': day, 'slots': _slots_for(now().date()), 'done': [], 'failed': {}}
        _save('schedule.json', plan)
    return plan


def braked():
    b = _load('brake.json', {})
    return b if b.get('until') and datetime.fromisoformat(b['until']) > now() else None


def brake(hours, reason):
    _save('brake.json', {'until': (now() + timedelta(hours=hours)).isoformat(), 'reason': reason, 'set': now().isoformat()})


def history():
    return _load('posted.json', [])


def due_slot():
    """The earliest slot that has passed and is not done, if the last post is at least MIN_GAP ago."""
    if braked():
        return None
    plan = today_plan()
    hist = history()
    if sum(1 for h in hist if h['time'][:10] == plan['day']) >= PER_DAY:  # hard daily cap from real history
        return None
    if hist and now() - datetime.fromisoformat(hist[-1]['time']) < MIN_GAP:
        return None
    for s in plan['slots']:
        t = datetime.fromisoformat(s)
        # a slot missed by more than STALE is skipped, never caught up: catching up would post in bursts
        if s not in plan['done'] and now() - STALE <= t <= now() and plan['failed'].get(s, 0) < 3:
            return s
    return None


def mark(slot, record=None, failed=False):
    plan = today_plan()
    if failed:
        plan['failed'][slot] = plan['failed'].get(slot, 0) + 1
    else:
        plan['done'].append(slot)
    _save('schedule.json', plan)
    if record:
        hist = history() + [record]
        _save('posted.json', hist[-500:])


def reel_media():
    """Every clip/photo already shown in a published Reel (never shown again)."""
    return set(_load('reel_media.json', []))


def add_reel_media(keys):
    _save('reel_media.json', sorted(reel_media() | set(keys)))


# ---- Reels: their own random daily times, a ready queue and their own brake --------------------------------------
REELS_PER_DAY = int(os.environ.get('REELS_PER_DAY', '10'))
REEL_QUEUE = os.path.join(STATE, 'reel_queue')
REEL_MAX_AGE = timedelta(hours=6)  # a ready Reel older than this is stale news and is thrown away


def reel_plan():
    plan = _load('reel_schedule.json', {})
    day = now().date().isoformat()
    if plan.get('day') != day:
        start = datetime(now().year, now().month, now().day, tzinfo=timezone.utc)
        rng = random.Random('reels-{}-{}'.format(day, os.environ.get('FB_PAGE_ID', '')))
        while True:
            mins = sorted(rng.uniform(0, 24 * 60) for _ in range(REELS_PER_DAY))
            if all(b - a >= 60 for a, b in zip(mins, mins[1:])):  # Reels at least an hour apart
                break
        plan = {'day': day, 'slots': [(start + timedelta(minutes=m)).isoformat() for m in mins], 'done': []}
        _save('reel_schedule.json', plan)
    return plan


def reel_braked():
    b = _load('reel_brake.json', {})
    return b if b.get('until') and datetime.fromisoformat(b['until']) > now() else None


def reel_brake(hours, reason):
    _save('reel_brake.json', {'until': (now() + timedelta(hours=hours)).isoformat(), 'reason': reason, 'set': now().isoformat()})


def reel_history():
    return _load('reels_posted.json', [])


def reel_due_slot():
    if braked() or reel_braked():
        return None
    plan = reel_plan()
    if sum(1 for h in reel_history() if h['time'][:10] == plan['day']) >= REELS_PER_DAY:
        return None
    for s in plan['slots']:
        t = datetime.fromisoformat(s)
        if s not in plan['done'] and now() - STALE <= t <= now():
            return s
    return None


def reel_queue():
    """Ready Reels, oldest first; stale ones are deleted."""
    import shutil
    os.makedirs(REEL_QUEUE, exist_ok=True)
    out = []
    for d in sorted(os.listdir(REEL_QUEUE)):
        meta = os.path.join(REEL_QUEUE, d, 'meta.json')
        if not os.path.exists(meta):
            continue
        m = json.load(open(meta))
        if now() - datetime.fromisoformat(m['built']) > REEL_MAX_AGE:
            shutil.rmtree(os.path.join(REEL_QUEUE, d), ignore_errors=True)
            continue
        out.append(dict(m, dir=os.path.join(REEL_QUEUE, d)))
    return out


def reel_mark(slot, record):
    plan = reel_plan()
    plan['done'].append(slot)
    _save('reel_schedule.json', plan)
    _save('reels_posted.json', (reel_history() + [record])[-500:])
