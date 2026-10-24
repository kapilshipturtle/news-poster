"""Random daily posting times, posting history and the safety brake. All state lives in state/*.json (committed by the workflow)."""
import json
import os
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .net import STATE

PER_DAY = int(os.environ.get('POSTS_PER_DAY', '24'))
MIN_GAP = timedelta(minutes=int(os.environ.get('MIN_GAP_MIN', '20')))
STALE = timedelta(minutes=60)
# User 2026-10-24: post only while the audience is awake, 06:00-23:00 on the Page's own clock (US Page: New York time,
# DST included; Hindi Page: LOCAL_TZ=Asia/Kolkata). A "day" (plans, daily caps) is that local day.
LOCAL = ZoneInfo(os.environ.get('LOCAL_TZ', 'America/New_York'))
WINDOW = tuple(int(x) for x in os.environ.get('POST_WINDOW', '360-1380').split('-'))  # minutes after local midnight
BUILD_EARLY, BUILD_STOP = 120, 20  # Reels are built from 2 h before the window opens until 20 min before it closes


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


def local_day(t=None):
    return (t or now()).astimezone(LOCAL).date()


def _day_of(iso):
    return datetime.fromisoformat(iso).astimezone(LOCAL).date().isoformat()


def _local_minute(tz=None, t=None):
    t = (t or now()).astimezone(tz or LOCAL)
    return t.hour * 60 + t.minute


def in_window():
    return WINDOW[0] <= _local_minute() < WINDOW[1]


def build_time(tz=None, window=None):
    """True while Reels for this window should be built (ready Reels expire after 6 h; night builds would be wasted)."""
    w = window or WINDOW
    return w[0] - BUILD_EARLY <= _local_minute(tz) < w[1] - BUILD_STOP


def _window_slots(day, n, gap_min, seed):
    """n random times inside the day's posting window, at least gap_min apart, as UTC iso strings. Seeded by the day,
    so a lost plan file regenerates the very same times instead of a fresh set (which could double-post)."""
    start = datetime(day.year, day.month, day.day, tzinfo=LOCAL)
    rng = random.Random(seed)
    while True:
        mins = sorted(rng.uniform(*WINDOW) for _ in range(n))
        if all(b - a >= gap_min for a, b in zip(mins, mins[1:])):
            return [(start + timedelta(minutes=m)).astimezone(timezone.utc).isoformat() for m in mins]


def _stale_plan(plan, day):
    return plan.get('day') != day or plan.get('window') != [str(LOCAL)] + list(WINDOW)


def today_plan():
    plan = _load('schedule.json', {})
    day = local_day()
    if _stale_plan(plan, day.isoformat()):
        slots = _window_slots(day, PER_DAY, MIN_GAP.total_seconds() / 60, '{}-{}'.format(day.isoformat(), os.environ.get('FB_PAGE_ID', '')))
        plan = {'day': day.isoformat(), 'window': [str(LOCAL)] + list(WINDOW), 'slots': slots, 'done': [], 'failed': {}}
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
    if braked() or not in_window():
        return None
    plan = today_plan()
    hist = history()
    if sum(1 for h in hist if _day_of(h['time']) == plan['day']) >= PER_DAY:  # hard daily cap from real history
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
# how far apart Reels go out (inside WINDOW); Hindi Page REEL_GAP_MIN=45
REEL_GAP = int(os.environ.get('REEL_GAP_MIN', '60'))
# catch-up (REEL_CATCHUP=1, both Pages since 2026-10-24): a slot that passed with no Reel ready is posted as soon as one is ready, still
# REEL_GAP minutes after the last Reel and within the daily cap and the window; off = a slot missed by over an hour is skipped
REEL_CATCHUP = os.environ.get('REEL_CATCHUP') == '1'


def reel_plan():
    plan = _load('reel_schedule.json', {})
    day = local_day()
    if _stale_plan(plan, day.isoformat()):
        slots = _window_slots(day, REELS_PER_DAY, REEL_GAP, 'reels-{}-{}'.format(day.isoformat(), os.environ.get('FB_PAGE_ID', '')))
        plan = {'day': day.isoformat(), 'window': [str(LOCAL)] + list(WINDOW), 'slots': slots, 'done': []}
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
    if braked() or reel_braked() or not in_window():
        return None
    plan = reel_plan()
    if sum(1 for h in reel_history() if _day_of(h['time']) == plan['day']) >= REELS_PER_DAY:
        return None
    if REEL_CATCHUP:
        hist = reel_history()
        if hist and now() - datetime.fromisoformat(hist[-1]['time']) < timedelta(minutes=REEL_GAP):
            return None
        past = [s for s in plan['slots'] if s not in plan['done'] and datetime.fromisoformat(s) <= now()]
        return past[0] if past else None
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
