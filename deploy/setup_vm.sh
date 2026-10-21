#!/bin/bash
# One-time (and re-runnable) setup of the news-poster on a Debian e2-micro VM. Run as root.
set -euo pipefail
if ! swapon --show | grep -q /swapfile; then   # e2-micro has 1 GB RAM; Chrome needs headroom
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv git wget fonts-noto-color-emoji
if ! command -v google-chrome >/dev/null; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  apt-get install -y -q /tmp/chrome.deb && rm /tmp/chrome.deb
fi
id poster >/dev/null 2>&1 || useradd -m -s /bin/bash poster
sudo -u poster bash -c '
  cd ~ && if [ -d news-poster ]; then git -C news-poster pull -q; else git clone -q https://github.com/kapilshipturtle/news-poster.git; fi
  [ -d venv ] || python3 -m venv venv
  venv/bin/pip install -q -r news-poster/requirements.txt'
install -m 644 /home/poster/news-poster/deploy/news-poster.service /etc/systemd/system/news-poster.service
install -m 644 /home/poster/news-poster/deploy/news-poster.timer /etc/systemd/system/news-poster.timer
systemctl daemon-reload
echo "setup done; enable with: systemctl enable --now news-poster.timer"
