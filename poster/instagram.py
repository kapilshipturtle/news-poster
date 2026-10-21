"""Cross-post to the Instagram professional account linked to the Page (official Instagram Graph API).
Instagram fetches the image from a public URL, so we hand it the CDN URL of the Facebook photo."""
import time

from .facebook import G, FacebookError, _call


def image_url(photo_id, token):
    """Largest public CDN URL of a Facebook photo."""
    imgs = _call('{}/{}?fields=images&access_token={}'.format(G, photo_id, token)).get('images', [])
    return max(imgs, key=lambda i: i.get('width', 0))['source'] if imgs else None


def hidden_photo(page_id, token, png):
    """Upload an image to the Page WITHOUT publishing it (not in the feed), only to get a public URL for Instagram."""
    return _call('{}/{}/photos'.format(G, page_id), {'published': 'false', 'temporary': 'true', 'access_token': token}, png)['id']


def quota_left(ig_id, token):
    d = _call('{}/{}/content_publishing_limit?fields=quota_usage,config&access_token={}'.format(G, ig_id, token)).get('data', [{}])[0]
    total = d.get('config', {}).get('quota_total')
    return None if total is None else total - d.get('quota_usage', 0)


def publish(ig_id, token, img_url, caption):
    container = _call('{}/{}/media'.format(G, ig_id), {'image_url': img_url, 'caption': caption[:2200], 'access_token': token})['id']
    for _ in range(20):  # Instagram processes the image asynchronously
        status = _call('{}/{}?fields=status_code&access_token={}'.format(G, container, token)).get('status_code')
        if status == 'FINISHED':
            break
        if status in ('ERROR', 'EXPIRED'):
            raise FacebookError(None, None, 'instagram container ' + status)
        time.sleep(3)
    media = _call('{}/{}/media_publish'.format(G, ig_id), {'creation_id': container, 'access_token': token})['id']
    link = _call('{}/{}?fields=permalink&access_token={}'.format(G, media, token)).get('permalink')
    return media, link
