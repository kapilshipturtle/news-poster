"""Dr. Olsson news poster.

  python3 run.py due              # prints due=true/false (and writes it to $GITHUB_OUTPUT)
  python3 run.py tick             # if a random slot is due: build one post and publish it
  python3 run.py tick --dry-run   # build only, never publish (output in work/<time>/)
  python3 run.py tick --force     # ignore the schedule (testing)
"""
import json
import os
import random
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from poster import ai, dedupe, facebook, gates, instagram, schedule, sources, xembed
from poster.net import WORK, load_env
from poster.render import render

MIN_LIKES = int(os.environ.get('MIN_TWEET_LIKES', '200'))


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
    # gather sources for the first picks; a story with a usable X post goes first (the page's strongest format)
    ready = []
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
        ready.append((pick, outlets, tweets))
        if tweets or len(ready) >= 3:
            break
    ready.sort(key=lambda r: not r[2])  # stable: X-post stories first, otherwise the trend order

    for pick, outlets, tweets in ready:
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
            photo = None
            # a photo of someone the headline doesn't name would mislead (headline: Hubbard, photo: Trump)
            if subject and dedupe.named_in(subject, out['headline']) and not dedupe.subject_recent(subject):
                photo = sources.wiki_photo(subject)
            if photo:
                (photo_url, _credit), kind = photo, 'photo'  # public domain: no credit line needed
            else:
                kind = random.choice(['typo', 'text'])
        png = None
        if kind != 'text':
            png = render(kind, out['headline'], pick.get('tag', 'NEWS'), page, work,
                         embed_png=tw['png'] if tw else None, photo_url=photo_url, fact=out.get('fact'))
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


def clean_work(days=3):
    """Working folders (images, embeds) are only needed for a few days; the VM disk is 30 GB."""
    cutoff = time.time() - days * 86400
    for d in os.listdir(WORK) if os.path.isdir(WORK) else []:
        path = os.path.join(WORK, d)
        if os.path.isdir(path) and d != 'selftest' and os.path.getmtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)


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
    clean_work()
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
        pid, photo_id = facebook.publish(os.environ['FB_PAGE_ID'], os.environ['FB_PAGE_TOKEN'], post['caption'], post['image'])
        info = facebook.check(pid, os.environ['FB_PAGE_TOKEN'])
    except facebook.FacebookError as e:
        log('FACEBOOK ERROR:', e)
        if e.blocked:  # Meta says slow down / blocked: stop everything for a day rather than push harder
            schedule.brake(24, str(e))
        schedule.mark(slot, failed=True)
        sys.exit(1)
    record.update(post_id=pid, permalink=info.get('permalink_url'), published=info.get('is_published'))
    if os.environ.get('IG_USER_ID'):
        record.update(cross_post_instagram(post, photo_id, work))
    schedule.mark(slot, record)
    log('PUBLISHED', info.get('permalink_url'))


def cross_post_instagram(post, photo_id, work):
    """Same post on the linked Instagram account. Never fails the Facebook post; Instagram needs an image,
    so a text post gets its headline card there."""
    ig, tok, page = os.environ['IG_USER_ID'], os.environ['FB_PAGE_TOKEN'], os.environ['FB_PAGE_ID']
    try:
        left = instagram.quota_left(ig, tok)
        if left is not None and left <= 0:
            log('INSTAGRAM skipped: daily API publishing quota used up')
            return {'ig_error': 'quota used up'}
        if not photo_id:
            png = render('typo', post['headline'], post.get('tag') or 'NEWS', os.environ.get('FB_PAGE_NAME', 'Dr. Olsson'),
                         os.path.join(work, 'ig'))
            photo_id = instagram.hidden_photo(page, tok, png)
        media, link = instagram.publish(ig, tok, instagram.image_url(photo_id, tok), post['caption'])
        log('INSTAGRAM PUBLISHED', link)
        return {'ig_media_id': media, 'ig_permalink': link}
    except Exception as e:  # noqa: BLE001 - Instagram must never take the Facebook post down with it
        log('INSTAGRAM ERROR:', e)
        if getattr(e, 'blocked', False):
            schedule.brake(24, 'instagram: {}'.format(e))
        return {'ig_error': str(e)[:300]}


def selftest():
    """Render all three image formats on this machine and run the image gate (no AI, no publishing)."""
    load_env()
    work = os.path.join(WORK, 'selftest')
    os.makedirs(work, exist_ok=True)
    page = os.environ.get('FB_PAGE_NAME', 'Dr. Olsson')
    tw = xembed.capture(['2112678669491544109'], work, min_likes=0, max_age=None)
    photo = sources.wiki_photo('Donald Trump')  # public-domain portrait
    results = {
        'tweet': render('tweet', 'Self test: official X embed', 'TEST', page, work + '/tweet', embed_png=tw[0]['png']) if tw else None,
        'photo': render('photo', 'Self test: free licensed photo', 'TEST', page, work + '/photo', photo_url=photo[0]) if photo else None,
        'typo': render('typo', 'Self test: bold text card', 'TEST', page, work + '/typo'),
    }
    bad = {k: (gates.image_problems(v, k) if v else ['could not build']) for k, v in results.items()}
    log('selftest:', bad)
    if any(bad.values()):
        sys.exit(1)


def reel_samples(count=2):
    """Build `count` Reels on different trending stories into work/reel-*/ and never publish them (for review)."""
    load_env()
    from poster import reel
    page = os.environ.get('FB_PAGE_NAME', 'Dr. Olsson')
    picks = ai.pick_stories(sources.trending(), dedupe.recent_headlines())
    made = 0
    for pick in picks:
        if made >= count:
            break
        work = os.path.join(WORK, 'reel-' + schedule.now().strftime('%Y%m%d-%H%M%S'))
        os.makedirs(work, exist_ok=True)
        outlets, embedded = sources.articles(pick['cluster'], pick['query'])
        if len(outlets) < 2:
            log('  skip (only {} outlets): {}'.format(len(outlets), pick['query']))
            continue
        t0 = time.time()
        try:
            res = reel.build(page, outlets, pick['title'], pick['query'], pick.get('subject'), pick.get('tag', 'NEWS'), work,
                             used=schedule.reel_media())
        except Exception as e:  # one story failing (e.g. no AI reply) never stops the run: the next story is tried
            log('  skip (error {}: {}): {}'.format(type(e).__name__, str(e)[:120], pick['query']))
            continue
        if res.get('skip', '').startswith('packed'):  # REEL_RENDER=pack: built, waiting for its Cloud Run render
            made += 1
            log('PACKED in {:.0f} s: {} -> {}'.format(time.time() - t0, pick['query'], res['skip']))
            continue
        if res.get('skip'):
            log('  skip ({}): {}'.format(res['skip'], pick['query']))
            continue
        made += 1
        log('REEL {} | {:.0f} s long | built in {:.0f} s | {} -> {}'.format(
            res['headline'], res['seconds'], time.time() - t0, res['moments'], res['video']))


def reel_selftest():
    """Every layout, motion, join and overlay rendered on this machine (about 12 s, silent voice); never published."""
    from poster import moments, reel
    import subprocess
    w = os.path.join(WORK, 'reel-selftest')
    os.makedirs(w, exist_ok=True)
    clip1 = moments.cut('-_sUwcKRoCc', 120, 3.6, os.path.join(w, 'c1.mp4'))
    clip2 = moments.cut('-_sUwcKRoCc', 400, 3.6, os.path.join(w, 'c2.mp4'))
    photo = moments.photo(sources.wiki_photo('Donald Trump')[0], os.path.join(w, 'p1.jpg'))
    srcs = [(clip1, 'clip', 'band', 'push_in', 'cut'), (photo, 'photo', 'full', 'pan_left', 'dissolve'),
            (clip2, 'clip', 'full', 'pull_out', 'flash'), (photo, 'photo', 'band', 'pan_right', 'punch')]
    plan, rendered, t = [], [], 0.0
    for n, (f, kind, layout, motion, into) in enumerate(srcs):
        nxt = srcs[n + 1][4] if n + 1 < len(srcs) else 'cut'
        plan.append({'a': t, 'b': t + 3.0, 'into': into, 'overlay': ['name0', None, 'fact0', None][n], 'moment': {'kind': kind}})
        rendered.append(reel.render_piece(f, kind, layout, motion, 3.0 + reel.JOIN[nxt][1], os.path.join(w, 's{}.mp4'.format(n)),
                                          [None, 'light_leak', None, 'wash_cool'][n], into == 'flash'))
        t += 3.0
    for kind, f, layout in (('clip', clip1, 'band'), ('photo', photo, 'full')):  # every effect, timed against a plain piece
        for fx in [None] + list(reel.EFFECTS) + ['flash']:
            t1 = time.time()
            reel.render_piece(f, kind, layout, 'push_in', 3.0, os.path.join(w, 'fx-{}-{}.mp4'.format(kind, fx)),
                              None if fx == 'flash' else fx, fx == 'flash')
            log('  effect {:10s} on a {} piece: {:.1f} s'.format(str(fx), kind, time.time() - t1))
    wav = os.path.join(w, 'silence.wav')
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '12', wav], check=True)
    lines = ['Atlanta just took control of the WNBA Finals.', 'Angel Reese scored 21 points and 12 rebounds.']
    ass = os.path.join(w, 'o.ass')
    reel._overlay_ass('Dream take 2-1 lead', 'Dr. Olsson', lines, [(0.25, 5.8), (6.0, 11.6)], 12.0, plan,
                      ['21 points, 12 rebounds'], ['Angel Reese'], ass)
    out = reel.assemble(rendered, plan, wav, ass, 12.0, os.path.join(w, 'selftest.mp4'))
    log('reel selftest ->', out, os.path.getsize(out))


def reel_build_one():
    """Keep up to 2 finished Reels ready (built ahead so build time never delays a post)."""
    import shutil
    from poster import reel
    load_env()
    if schedule.braked() or schedule.reel_braked() or len(schedule.reel_queue()) >= 2:
        return
    page = os.environ.get('FB_PAGE_NAME', 'Dr. Olsson')
    clean_work()
    recent = [h['headline'] for h in schedule.reel_history()[-30:]] + [q['headline'] for q in schedule.reel_queue()]
    picks = ai.pick_stories(sources.trending(), recent)
    failed = {q: t for q, t in schedule._load('reel_failed.json', {}).items()
              if schedule.now() - datetime.fromisoformat(t) < timedelta(hours=24)}

    def fail(query):  # a story that could not become a Reel is not tried again for 24 h (each try costs 10+ min)
        failed[query] = schedule.now().isoformat()
        schedule._save('reel_failed.json', failed)
    for pick in picks:
        if any(dedupe._overlap(dedupe.words(pick['title']), dedupe.words(r)) >= dedupe.LIMIT for r in recent):
            continue  # this story already has a Reel (a Reel on a story that was also a photo post is fine)
        if pick['query'] in failed:
            continue
        work = os.path.join(WORK, 'reel-' + schedule.now().strftime('%Y%m%d-%H%M%S'))
        os.makedirs(work, exist_ok=True)
        outlets, _ = sources.articles(pick['cluster'], pick['query'])
        if len(outlets) < 2:
            fail(pick['query'])
            continue
        try:
            res = reel.build(page, outlets, pick['title'], pick['query'], pick.get('subject'), pick.get('tag', 'NEWS'), work,
                             used=schedule.reel_media() | {k for q in schedule.reel_queue() for k in q.get('media_keys', [])})
        except Exception as e:  # one story failing never stops the builder
            log('  reel skip (error {}: {})'.format(type(e).__name__, str(e)[:120]))
            fail(pick['query'])
            continue
        if res.get('skip'):
            log('  reel skip ({}): {}'.format(res['skip'], pick['query']))
            fail(pick['query'])
            continue
        dest = os.path.join(schedule.REEL_QUEUE, os.path.basename(work))
        os.makedirs(dest, exist_ok=True)
        shutil.copy(res['video'], os.path.join(dest, 'reel.mp4'))
        json.dump({'built': schedule.now().isoformat(), 'headline': res['headline'], 'caption': res['caption'],
                   'seconds': res['seconds'], 'media_keys': res['media_keys'], 'title': pick['title'], 'query': pick['query']},
                  open(os.path.join(dest, 'meta.json'), 'w'), indent=1)
        shutil.rmtree(work, ignore_errors=True)  # the queue keeps its own copy; working files are ~200 MB a Reel
        log('REEL READY', res['headline'], res['moments'])
        return


def reel_publish():
    """Publish the oldest ready Reel when one of today's random Reel times is due. A Reel is marked published the
    moment Facebook accepts it (before the status polling), and a Reel whose upload ended in an unknown state is
    dropped, never retried: the same Reel can never go out twice."""
    import shutil
    load_env()
    slot = schedule.reel_due_slot()
    queue = schedule.reel_queue()
    if not (slot and queue):
        return
    r = queue[0]
    tok = os.environ['FB_PAGE_TOKEN']
    try:
        vid = facebook.publish_reel(os.environ['FB_PAGE_ID'], tok, os.path.join(r['dir'], 'reel.mp4'), r['caption'])
    except facebook.FacebookError as e:  # Facebook refused it: nothing went out
        log('REEL FACEBOOK ERROR:', e)
        schedule.reel_brake(48 if e.blocked else 6, str(e))
        return
    except Exception as e:  # noqa: BLE001 - a network error mid-upload: it may or may not be live, so it is dropped
        log('REEL UPLOAD ENDED UNKNOWN ({}: {}), Reel dropped, Reels paused 6 h'.format(type(e).__name__, str(e)[:200]))
        schedule.add_reel_media(r['media_keys'])
        shutil.rmtree(r['dir'], ignore_errors=True)
        schedule.reel_brake(6, 'upload ended unknown: {}'.format(type(e).__name__))
        return
    schedule.add_reel_media(r['media_keys'])
    record = {'time': schedule.now().isoformat(), 'slot': slot, 'video_id': vid, 'headline': r['headline'], 'title': r['title']}
    schedule.reel_mark(slot, record)
    shutil.rmtree(r['dir'], ignore_errors=True)
    log('REEL PUBLISHED', vid, r['headline'])
    status = {}
    try:
        for _ in range(40):  # Meta processes the upload; wait for the publishing result
            time.sleep(15)
            status = facebook.reel_status(vid, tok).get('status', {})
            if status.get('video_status') in ('ready', 'error') or (status.get('publishing_phase') or {}).get('status') in ('complete', 'error'):
                break
    except Exception as e:  # noqa: BLE001 - the Reel is already out; only its status is unknown
        log('REEL status check failed ({}: {}); the Reel itself was accepted'.format(type(e).__name__, str(e)[:200]))
        return
    bad = status.get('video_status') == 'error' or (status.get('publishing_phase') or {}).get('status') == 'error' \
        or (status.get('processing_phase') or {}).get('status') == 'error'
    if bad:  # Meta rejected or flagged it (copyright included): Reels pause for 48 h
        schedule.reel_brake(48, 'reel status: {}'.format(json.dumps(status)[:300]))
        log('REEL REJECTED, Reels paused 48 h:', json.dumps(status)[:300])
    else:
        log('REEL STATUS', json.dumps(status)[:300])


def reel_tick():
    reel_publish()
    reel_build_one()


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
    elif cmd == 'reel-samples':
        reel_samples(int(sys.argv[2]) if len(sys.argv) > 2 else 2)
    elif cmd == 'reel-tick':
        reel_tick()
    elif cmd == 'reel-publish':
        reel_publish()
    elif cmd == 'reel-build':
        reel_build_one()
    elif cmd == 'reel-selftest':
        load_env()
        reel_selftest()
    elif cmd == 'selftest':
        selftest()
    elif cmd == 'tick':
        tick(dry='--dry-run' in sys.argv, force='--force' in sys.argv)
    else:
        sys.exit(__doc__)
