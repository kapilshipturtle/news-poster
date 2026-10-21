"""Turn a fresh FB_USER_TOKEN (Graph API Explorer) into a non-expiring Page token, find the linked Instagram account,
and save both to ~/.config/fb-news/secrets.env. Never prints a token.

usage: python3 tools/setup_token.py <page-id>
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request

PATH = os.path.expanduser('~/.config/fb-news/secrets.env')
G = 'https://graph.facebook.com'


def call(path, **params):
    try:
        with urllib.request.urlopen('{}/{}?{}'.format(G, path, urllib.parse.urlencode(params)), timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read()).get('error', {})
        sys.exit('Meta error on /{}: {} {}'.format(path, err.get('code'), err.get('message')))


env = {}
for line in open(PATH):  # parsed in Python: values contain spaces ("Dr. Olsson"), so never `source` this file
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.strip().split('=', 1)
        env[k] = v
page_id = sys.argv[1] if len(sys.argv) > 1 else env.get('FB_PAGE_ID')
long = call('oauth/access_token', grant_type='fb_exchange_token', client_id=env['FB_APP_ID'],
            client_secret=env['FB_APP_SECRET'], fb_exchange_token=env['FB_USER_TOKEN'])
# works for Pages reached through a business portfolio too (they are missing from /me/accounts)
page = call(page_id, fields='id,name,access_token,instagram_business_account{id,username}', access_token=long['access_token'])
info = call('debug_token', input_token=page['access_token'], access_token='{}|{}'.format(env['FB_APP_ID'], env['FB_APP_SECRET']))['data']
print('page:', page['name'], '| token valid:', info.get('is_valid'), '| expires_at:', info.get('expires_at'), '(0 = never)')
print('scopes:', info.get('scopes'))
env.update(FB_USER_TOKEN=long['access_token'], FB_PAGE_ID=page['id'], FB_PAGE_NAME=page['name'], FB_PAGE_TOKEN=page['access_token'])
ig = page.get('instagram_business_account')
if ig:
    env['IG_USER_ID'] = ig['id']
    print('instagram: @{} (id {})'.format(ig.get('username'), ig['id']))
else:
    print('instagram: NONE linked to this Page (needs a professional account linked in Page settings)')
for need in ('instagram_basic', 'instagram_content_publish'):
    if need not in info.get('scopes', []):
        print('missing permission for Instagram:', need)
with open(PATH + '.tmp', 'w') as f:
    f.write(''.join('{}={}\n'.format(k, v) for k, v in env.items()))
os.chmod(PATH + '.tmp', 0o600)
os.replace(PATH + '.tmp', PATH)
print('saved to', PATH)
