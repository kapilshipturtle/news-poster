"""Publish to the Page through the official Graph API and read the post back."""
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

G = 'https://graph.facebook.com'
# Meta's "you are temporarily blocked / spam" family: stop posting instead of retrying
BLOCK_CODES = {368, 32, 4, 17, 341}


class FacebookError(Exception):
    def __init__(self, code, sub, msg):
        super().__init__('code {} / {}: {}'.format(code, sub, msg))
        self.code, self.blocked = code, code in BLOCK_CODES or 'spam' in (msg or '').lower() or 'blocked' in (msg or '').lower()


def _smallest(png):
    """PNG or JPEG q90, whichever is smaller: the free server has 1 GB/month of outbound traffic."""
    raw = open(png, 'rb').read()
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.open(png).convert('RGB').save(buf, 'JPEG', quality=90)
        if buf.tell() < len(raw):
            return buf.getvalue(), 'jpg'
    except ImportError:
        pass
    return raw, 'png'


def _call(url, fields=None, image=None):
    if image is None:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode() if fields else None)
    else:
        b = uuid.uuid4().hex
        parts = [('--{}\r\nContent-Disposition: form-data; name="{}"\r\n\r\n{}\r\n'.format(b, k, v)).encode()
                 for k, v in fields.items()]
        data, kind = _smallest(image)
        parts.append(('--{}\r\nContent-Disposition: form-data; name="source"; filename="image.{}"\r\n'
                      'Content-Type: image/{}\r\n\r\n').format(b, kind, 'jpeg' if kind == 'jpg' else 'png').encode() + data + b'\r\n')
        req = urllib.request.Request(url, data=b''.join(parts) + '--{}--\r\n'.format(b).encode(),
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + b})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read() or b'{}').get('error', {})
        raise FacebookError(err.get('code'), err.get('error_subcode'), err.get('message'))


def publish(page_id, token, caption, image=None):
    """Returns (post_id, photo_id); photo_id is None for a text post."""
    if image:
        res = _call('{}/{}/photos'.format(G, page_id), {'message': caption, 'published': 'true', 'access_token': token}, image)
        return res.get('post_id') or res['id'], res['id']
    return _call('{}/{}/feed'.format(G, page_id), {'message': caption, 'access_token': token})['id'], None


def check(post_id, token):
    return _call('{}/{}?{}'.format(G, post_id, urllib.parse.urlencode(
        {'fields': 'id,created_time,permalink_url,is_published', 'access_token': token})))
