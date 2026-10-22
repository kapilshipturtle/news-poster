"""Cloud Run render worker: POST /render {"bucket", "job"} -> downloads <job>/in.tar.gz from GCS, renders every piece
and assembles the Reel with poster.reel (the same code as the server), uploads <job>/reel.mp4 and returns timings.
Credentials come from Cloud Run's own service account (metadata server); nothing secret is in this image."""
import json
import os
import tarfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

from poster import reel

WORKERS = int(os.environ.get('WORKERS', '4'))


def token():
    req = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                                 headers={'Metadata-Flavor': 'Google'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())['access_token']


def gcs_get(bucket, name, path):
    url = 'https://storage.googleapis.com/storage/v1/b/{}/o/{}?alt=media'.format(bucket, urllib.parse.quote(name, safe=''))
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token()}), timeout=300) as r, \
            open(path, 'wb') as f:
        f.write(r.read())


def gcs_put(bucket, name, path, ctype):
    url = 'https://storage.googleapis.com/upload/storage/v1/b/{}/o?uploadType=media&name={}'.format(bucket, urllib.parse.quote(name, safe=''))
    req = urllib.request.Request(url, data=open(path, 'rb').read(), method='POST',
                                 headers={'Authorization': 'Bearer ' + token(), 'Content-Type': ctype})
    urllib.request.urlopen(req, timeout=300).read()


def render(bucket, job):
    t, marks = time.time(), {}
    work = '/tmp/' + job.replace('/', '_')
    os.makedirs(work, exist_ok=True)
    gcs_get(bucket, job + '/in.tar.gz', work + '/in.tar.gz')
    tarfile.open(work + '/in.tar.gz').extractall(work)
    marks['download_s'] = round(time.time() - t, 1)
    spec = json.load(open(work + '/job.json'))  # pieces: [{src, kind, layout, motion, seconds, effect, flash_in}], plan, total
    os.environ['SFX_DIR'] = work + '/sfx'
    t1 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        rendered = list(ex.map(lambda kv: reel.render_piece(work + '/' + kv[1]['src'], kv[1]['kind'], kv[1]['layout'], kv[1]['motion'],
                                                            kv[1]['seconds'], '{}/seg{:02d}.mp4'.format(work, kv[0]),
                                                            kv[1].get('effect'), kv[1].get('flash_in', False)),
                               enumerate(spec['pieces'])))
    marks['render_pieces_s'] = round(time.time() - t1, 1)
    t2 = time.time()
    video = reel.assemble(rendered, spec['plan'], work + '/voice.wav', work + '/overlay.ass', spec['total'], work + '/reel.mp4')
    marks['assemble_s'] = round(time.time() - t2, 1)
    gcs_put(bucket, job + '/reel.mp4', video, 'video/mp4')
    marks['total_s'] = round(time.time() - t, 1)
    marks['cpus'] = os.cpu_count()
    return marks


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        try:
            out, code = render(body['bucket'], body['job']), 200
        except Exception as e:  # noqa: BLE001 - reported to the caller
            out, code = {'error': '{}: {}'.format(type(e).__name__, e)}, 500
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')


HTTPServer(('', int(os.environ.get('PORT', '8080'))), H).serve_forever()
