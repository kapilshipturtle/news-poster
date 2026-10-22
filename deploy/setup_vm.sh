#!/bin/bash
# One-time (and re-runnable) setup of the news-poster on a Debian e2-micro VM. Run as root.
set -euo pipefail
if ! swapon --show | grep -q /swapfile; then   # e2-micro has 1 GB RAM; Chrome needs headroom
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv git wget fonts-noto-color-emoji ffmpeg tesseract-ocr
if ! command -v google-chrome >/dev/null; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  apt-get install -y -q /tmp/chrome.deb && rm /tmp/chrome.deb
fi
id poster >/dev/null 2>&1 || useradd -m -s /bin/bash poster
# every folder under the poster's home must belong to poster: a root-owned ~/.config made Chrome crash (SIGTRAP)
install -d -o poster -g poster -m 755 /home/poster/.config
install -d -o poster -g poster -m 700 /home/poster/.config/fb-news
install -d -o poster -g poster -m 755 /home/poster/state
sudo -u poster bash -c '
  cd ~ && if [ -d news-poster ]; then git -C news-poster pull -q; else git clone -q https://github.com/kapilshipturtle/news-poster.git; fi
  [ -d venv ] || python3 -m venv venv
  venv/bin/pip install -q -r news-poster/requirements.txt -r news-poster/requirements-reel.txt
  mkdir -p models && cd models   # Kokoro voice model, Apache-2.0
  [ -f kokoro-v1.0.int8.onnx ] || wget -q https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
  [ -f voices-v1.0.bin ] || wget -q https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin'
install -m 644 /home/poster/news-poster/deploy/news-poster.service /etc/systemd/system/news-poster.service
install -m 644 /home/poster/news-poster/deploy/news-poster.timer /etc/systemd/system/news-poster.timer
systemctl daemon-reload
echo "setup done; enable with: systemctl enable --now news-poster.timer"
