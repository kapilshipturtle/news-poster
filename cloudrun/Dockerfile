# Render worker for Reels on Google Cloud Run (same Debian 12 ffmpeg 5.1 as the e2-micro server, same code)
FROM python:3.11-slim-bookworm
RUN apt-get update -q && apt-get install -y -q --no-install-recommends ffmpeg fontconfig && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow==10.4.0 numpy==2.4.6
WORKDIR /app
COPY poster/ /app/poster/
COPY assets/fonts/ /app/assets/fonts/
COPY cloudrun/server.py /app/server.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
