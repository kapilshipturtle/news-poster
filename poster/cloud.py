"""Reel rendering on Google Cloud Run (cloudrun/server.py, the same poster.reel code). MEASURED on Reel 1 (19 pieces):
the e2-micro rendered + assembled in 1530 s, Cloud Run (4 vCPU) in 75.9 s. The job travels through the render bucket
(same region as both machines: no transfer cost); the VM's own service account (reel-caller) signs every request.
Env: CLOUD_RENDER_URL, CLOUD_RENDER_BUCKET. Any failure returns None and the caller renders on this machine."""
import json
import os
import tarfile
import time
import urllib.parse
import urllib.request

URL = os.environ.get('CLOUD_RENDER_URL', '')
BUCKET = os.environ.get('CLOUD_RENDER_BUCKET', '')
META = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/'


def _meta(path):
    req = urllib.request.Request(META + path, headers={'Metadata-Flavor': 'Google'})
    return urllib.request.urlopen(req, timeout=10).read().decode()


def _gcs(method, name, path=None):
    tok = json.loads(_meta('token'))['access_token']
    q = urllib.parse.quote(name, safe='')
    if method == 'PUT':
        url = 'https://storage.googleapis.com/upload/storage/v1/b/{}/o?uploadType=media&name={}'.format(BUCKET, q)
        req = urllib.request.Request(url, data=open(path, 'rb').read(), method='POST',
                                     headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/gzip'})
    elif method == 'GET':
        url = 'https://storage.googleapis.com/storage/v1/b/{}/o/{}?alt=media'.format(BUCKET, q)
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok})
    else:
        url = 'https://storage.googleapis.com/storage/v1/b/{}/o/{}'.format(BUCKET, q)
        req = urllib.request.Request(url, method='DELETE', headers={'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    if method == 'GET':
        open(path, 'wb').write(data)


def pack(work, plan, files, seg_lens, wav, ass, total, sfx_dir):
    """<work>/in.tar.gz: the sources, voice, captions, sound effects and job.json the worker renders from."""
    pieces = [{'src': os.path.basename(f), 'kind': p['moment']['kind'], 'layout': p['layout'], 'motion': p['motion'],
               'seconds': seg_lens[n], 'effect': p.get('effect'), 'flash_in': p['into'] == 'flash', 'cx': p['moment'].get('cx')}
              for n, (p, f) in enumerate(zip(plan, files))]
    job = os.path.join(work, 'job.json')
    json.dump({'pieces': pieces, 'plan': plan, 'total': total}, open(job, 'w'), default=str)
    out = os.path.join(work, 'in.tar.gz')
    with tarfile.open(out, 'w:gz') as t:
        for f in list(files) + [wav, ass, job]:
            t.add(f, arcname=os.path.basename(f) if f not in (wav, ass) else {wav: 'voice.wav', ass: 'overlay.ass'}[f])
        if os.path.isdir(sfx_dir):
            for f in sorted(os.listdir(sfx_dir)):
                if f.endswith('.wav'):
                    t.add(os.path.join(sfx_dir, f), arcname='sfx/' + f)
    return out


def render(tar, video):
    """Upload the job, render on Cloud Run, download the Reel. Returns the worker's timings or None."""
    if not (URL and BUCKET):
        return None
    name = 'jobs/{}-{}'.format(os.path.basename(os.path.dirname(tar)), int(time.time()))
    try:
        _gcs('PUT', name + '/in.tar.gz', tar)
        idt = _meta('identity?audience=' + urllib.parse.quote(URL, safe=''))
        req = urllib.request.Request(URL + '/render', data=json.dumps({'bucket': BUCKET, 'job': name}).encode(), method='POST',
                                     headers={'Authorization': 'Bearer ' + idt, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=1800) as r:
            marks = json.loads(r.read())
        _gcs('GET', name + '/reel.mp4', video)
        for f in ('in.tar.gz', 'reel.mp4'):  # the bucket also deletes everything after a day
            try:
                _gcs('DELETE', name + '/' + f)
            except Exception:  # noqa: BLE001 - lifecycle cleans up
                pass
        return marks
    except Exception as e:  # noqa: BLE001 - reported; the Reel is rendered on this machine instead
        print('  cloud render failed, rendering here: {}: {}'.format(type(e).__name__, str(e)[:200]), flush=True)
        return None
