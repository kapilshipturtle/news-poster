"""Checks every post must pass before it can be published."""
import os
import re

BAIT = re.compile(r"\b(comment (yes|no|below|\d)|type (yes|amen|\d)|share (this|if)|like if|tag (a|your) (friend|someone)|"
                  r"who agrees|smash (that|the) like|react with|1 ?like ?=|if you agree)\b", re.I)
AI_TELLS = re.compile(r"\b(delve|tapestry|testament to|game[- ]changer|buckle up|in a world where|here's the thing|"
                      r"let that sink in|breaking news)\b", re.I)
URL = re.compile(r'https?://|www\.|\.com/\S', re.I)
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')


def tidy(caption):
    """Mechanical fixes that never need a rewrite."""
    caption = caption.replace(' — ', ', ').replace('—', ', ').replace(' – ', ', ')
    return re.sub(r'\n{3,}', '\n\n', caption).strip()


def text_problems(headline, caption):
    p = []
    words = len(caption.split())
    if not 30 <= words <= 150:
        p.append('caption is {} words, must be 40-110'.format(words))
    if BAIT.search(caption):
        p.append('engagement bait phrase: "{}"'.format(BAIT.search(caption).group(0)))
    if AI_TELLS.search(caption + ' ' + headline):
        p.append('AI-sounding phrase: "{}"'.format(AI_TELLS.search(caption + ' ' + headline).group(0)))
    if URL.search(caption):
        p.append('caption contains a link')
    if len(EMOJI.findall(caption)) > 3:
        p.append('more than 2 emoji')
    if caption.count('#') > 2:
        p.append('more than 2 hashtags')
    hw = len(headline.split())
    if not 3 <= hw <= 12:
        p.append('headline is {} words, must be 3-10'.format(hw))
    if EMOJI.search(headline):
        p.append('emoji in headline')
    return p


def image_problems(png, kind=None):
    if not os.path.exists(png):
        return ['image missing']
    if os.path.getsize(png) < 15000:  # an all-white 1080x1350 PNG is ~5 KB; a short headline card ~28 KB
        return ['image only {} bytes (blank render?)'.format(os.path.getsize(png))]
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return []
    im = Image.open(png).convert('L')
    if im.size != (1080, 1350):
        return ['image is {}x{}, expected 1080x1350'.format(*im.size)]
    if ImageStat.Stat(im).stddev[0] < 25:
        return ['image looks blank (stddev {:.0f})'.format(ImageStat.Stat(im).stddev[0])]
    # the picture area alone must have real content (a failed photo/embed leaves it white)
    area = {'photo': (0, 0, 1080, 760), 'tweet': (64, 420, 1016, 1250)}.get(kind)
    if area and ImageStat.Stat(im.crop(area)).stddev[0] < 20:
        return ['{} area is blank (stddev {:.0f})'.format(kind, ImageStat.Stat(im.crop(area)).stddev[0])]
    return []
