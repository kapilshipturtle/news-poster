#!/bin/bash
# Every 6 h: old working folders, old temp files, journal size, pip/apt caches. Once a week: yt-dlp update (YouTube
# changes break old versions), taken under the shared build lock so no download runs during the update.
set -u
W=/home/poster/news-poster/work
before=$(df --output=used -m / | tail -1)
find "$W" -mindepth 1 -maxdepth 1 -type d ! -name selftest -mmin +720 -exec rm -rf {} + 2>/dev/null
find /tmp -mindepth 1 -maxdepth 1 -mtime +1 ! -name 'systemd-private-*' -exec rm -rf {} + 2>/dev/null
journalctl --vacuum-size=300M -q
rm -rf /home/poster/.cache/pip /root/.cache/pip
apt-get clean -q
S=/var/lib/news-ytdlp.updated
if [ ! -f "$S" ] || [ $(( $(date +%s) - $(stat -c %Y "$S") )) -gt 604800 ]; then
  if flock -w 3600 /home/poster/state/.reel.lock sudo -u poster /home/poster/venv/bin/pip install -q -U yt-dlp; then
    touch "$S"
  fi
  echo "yt-dlp $(/home/poster/venv/bin/yt-dlp --version)"
fi
echo "cleanup: freed $(( before - $(df --output=used -m / | tail -1) )) MB, disk $(df -h --output=pcent / | tail -1 | tr -d ' ') used"
