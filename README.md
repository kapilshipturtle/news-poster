# news-poster

Posts trending US news to a Facebook Page 24 times a day at random times, through the official Graph API.

Every post passes these gates or is not published:
- story carried by 2+ outlets (fact check from their article text)
- never a duplicate: same story (72 h), same X post, same caption, same main subject (12 h)
- no engagement bait, no links, no clickbait headline (Meta demotes all three)
- caption written in our own words with a question, X posts credited
- image actually rendered (blank photo/embed areas rejected)
- if Meta answers with a spam/limit error, posting stops for 24 h (`state/brake.json`)

Sources: publishers' RSS feeds, the article pages, [GDELT](https://www.gdeltproject.org/) (open data), X's official embed widget, Wikimedia Commons (credited). Captions: Gemini API (non-Preview models).

Runs on a free Google Cloud e2-micro VM (`deploy/setup_vm.sh`, systemd timer every 5 min, state in `/home/poster/state`).
Secrets: `/home/poster/.config/fb-news/secrets.env` on the VM (`FB_PAGE_ID`, `FB_PAGE_NAME`, `FB_PAGE_TOKEN`, `GEMINI_API_KEY`).
GitHub Actions is used only for testing (`dry-run` workflow, never publishes), per GitHub's Actions terms.

Test locally: `python3 run.py selftest` and `python3 run.py tick --dry-run --force`.
