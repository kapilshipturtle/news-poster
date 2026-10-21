"""Dr. Olsson news poster.

  python3 run.py due              # prints due=true/false (and writes it to $GITHUB_OUTPUT)
  python3 run.py tick             # if a random slot is due: build one post and publish it
  python3 run.py tick --dry-run   # build only, never publish (output in work/<time>/)
  python3 run.py tick --force     # ignore the schedule (testing)
"""
import json
import os
import random
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from poster import ai, dedupe, facebook, gates, schedule, sources, xembed
from poster.net import WORK, load_env
from poster.render import render

MIN_LIKES = int(os.environ.get('MIN_TWEET_LIKES', '500'))


class NoStory(Exception):
    pass


def log(*a):
    print(*a, flush=True)


def build(page, work):
    """Pick a fresh trending story that passes every gate and make its post. Returns the post dict."""
    cands = sources.trending()
    log('trending clusters:', len(cands), '| top:', [(len(c['outlets']), c['title'][:50]) for c in cands[:5]])
    picks = ai.pick_stories(cands, dedupe.recent_headlines())
    log('picks:', [p['title'][:60] for p in picks])
    for pick in picks:
        dup = dedupe.story_seen(pick['title'], pick['query'])
        if dup:
            log('  skip (already posted as "{}"): {}'.format(dup, pick['title'][:60]))
            continue
        outlets, embedded = sources.articles(pick['cluster'], pick['query'])
        if len(outlets) < 2:
            log('  skip (only {} outlets with text): {}'.format(len(outlets), pick['query']))
            continue
        ids = [t for t in embedded if not dedupe.tweet_used(t)]
        tweets = xembed.capture(ids, work, min_likes=MIN_LIKES) if ids else []
        log('  {} | outlets {} | X posts embedded {} usable {}'.format(
            pick['query'], [o['outlet'] for o in outlets], len(ids), len(tweets)))

        out, problems = None, None
        for _ in range(2):
            out = ai.write_post(page, outlets, tweets, fix=problems)
            if out.get('skip'):
                break
            out['caption'] = gates.tidy(out.get('caption', ''))
            problems = gates.text_problems(out.get('headline', ''), out['caption'])
            if not problems:
                break
            log('  rewrite:', problems)
        if out.get('skip') or problems:
            log('  skip ({}): {}'.format(out.get('skip') or problems, pick['query']))
            continue
        dup = (dedupe.story_seen(pick['title'], pick['query'], out['headline'])
               or ai.same_event('{} / {}'.format(pick['title'], out['headline']), dedupe.recent_headlines()))
        if dup or dedupe.caption_used(out['caption']):
            log('  skip (duplicate of "{}")'.format(dup))
            continue

        tw, subject, caption, photo_url = None, pick.get('subject'), out['caption'], None
        ti = out.get('tweet_index')
        if isinstance(ti, int) and 0 <= ti < len(tweets):
            tw, kind = tweets[ti], 'tweet'
        else:
            photo = None if dedupe.subject_recent(subject) else (sources.wiki_photo(subject) if subject else None)
            if photo:
                (photo_url, credit), kind = photo, 'photo'
                caption += '\n\n' + credit
            else:
                kind = random.choice(['typo', 'text'])
        png = None
        if kind != 'text':
            png = render(kind, out['headline'], pick.get('tag', 'NEWS'), page, work,
                         embed_png=tw['png'] if tw else None, photo_url=photo_url)
            bad = gates.image_problems(png, kind)
            if bad:
                log('  skip (image: {})'.format(bad))
                continue
        open(os.path.join(work, 'caption.txt'), 'w').write(caption + '\n')
        return {'kind': kind, 'headline': out['headline'], 'caption': caption, 'image': png, 'title': pick['title'],
                'query': pick['query'], 'tag': pick.get('tag'), 'subject': subject if kind == 'photo' else None,
                'outlets': [o['url'] for o in outlets], 'tweet_id': tw['id'] if tw else None,
                'tweet_url': 'https://x.com/{}/status/{}'.format(tw['handle'], tw['id']) if tw else None}
    raise NoStory('no candidate passed every gate')


def tick(dry=False, force=False):
    load_env()
    brake = schedule.braked()
    if brake:
        log('BRAKED until', brake['until'], '-', brake['reason'])
        return
    slot = schedule.due_slot() or (schedule.now().isoformat() if force else None)
    if not slot:
        log('nothing due')
        return
    page = os.environ.get('FB_PAGE_NAME', 'Dr. Olsson')
    work = os.path.join(WORK, schedule.now().strftime('%Y%m%d-%H%M%S'))
    os.makedirs(work, exist_ok=True)
    try:
        post = build(page, work)
    except Exception as e:
        log('BUILD FAILED:', e)
        traceback.print_exc()
        if not force:
            schedule.mark(slot, failed=True)
        sys.exit(1)
    log('built [{}] {}'.format(post['kind'], post['headline']))
    log(post['caption'])
    record = dict(post, time=schedule.now().isoformat(), slot=slot, caption_hash=dedupe.caption_hash(post['caption']))
    record.pop('caption'), record.pop('image')
    if dry:
        json.dump(record, open(os.path.join(work, 'post.json'), 'w'), indent=1)
        log('DRY RUN: not published ->', work)
        return
    try:
        pid = facebook.publish(os.environ['FB_PAGE_ID'], os.environ['FB_PAGE_TOKEN'], post['caption'], post['image'])
        info = facebook.check(pid, os.environ['FB_PAGE_TOKEN'])
    except facebook.FacebookError as e:
        log('FACEBOOK ERROR:', e)
        if e.blocked:  # Meta says slow down / blocked: stop everything for a day rather than push harder
            schedule.brake(24, str(e))
        schedule.mark(slot, failed=True)
        sys.exit(1)
    record.update(post_id=pid, permalink=info.get('permalink_url'), published=info.get('is_published'))
    schedule.mark(slot, record)
    log('PUBLISHED', info.get('permalink_url'))


def selftest():
    """Render all three image formats on this machine and run the image gate (no AI, no publishing)."""
    load_env()
    work = os.path.join(WORK, 'selftest')
    os.makedirs(work, exist_ok=True)
    page = os.environ.get('FB_PAGE_NAME', 'Dr. Olsson')
    tw = xembed.capture(['2112678669491544109'], work, min_likes=0, max_age=None)
    photo = sources.wiki_photo('Elon Musk')
    results = {
        'tweet': render('tweet', 'Self test: official X embed', 'TEST', page, work + '/tweet', embed_png=tw[0]['png']) if tw else None,
        'photo': render('photo', 'Self test: free licensed photo', 'TEST', page, work + '/photo', photo_url=photo[0]) if photo else None,
        'typo': render('typo', 'Self test: bold text card', 'TEST', page, work + '/typo'),
    }
    bad = {k: (gates.image_problems(v, k) if v else ['could not build']) for k, v in results.items()}
    log('selftest:', bad)
    if any(bad.values()):
        sys.exit(1)


def due():
    load_env()
    d = bool(schedule.due_slot())
    log('due={}'.format(str(d).lower()))
    if os.environ.get('GITHUB_OUTPUT'):
        open(os.environ['GITHUB_OUTPUT'], 'a').write('due={}\n'.format(str(d).lower()))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'tick'
    if cmd == 'due':
        due()
    elif cmd == 'selftest':
        selftest()
    elif cmd == 'tick':
        tick(dry='--dry-run' in sys.argv, force='--force' in sys.argv)
    else:
        sys.exit(__doc__)
