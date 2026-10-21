"""HTTP helpers and settings shared by every module."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get('STATE_DIR', os.path.join(ROOT, 'state'))  # the VM keeps it outside the git checkout
WORK = os.path.join(ROOT, 'work')


def load_env():
    """Secrets come from the environment (GitHub Actions) or ~/.config/fb-news/secrets.env (laptop)."""
    path = os.path.expanduser('~/.config/fb-news/secrets.env')
    if os.path.exists(path):
        for line in open(path):
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def get(url, timeout=20, raw=False, headers=None):
    h = {'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        body = r.read()
    return body if raw else body.decode('utf-8', 'ignore')


def get_json(url, timeout=20):
    return json.loads(get(url, timeout))


def q(s):
    return urllib.parse.quote(s)
