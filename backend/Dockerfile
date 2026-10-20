# Flask backend: API, speech recognition (faster-whisper), NLP, minutes generation, storage.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models TEMP_DIR=/data/tmp_audio

# ffmpeg: the backend measures real audio levels with it. fonts-dejavu-core: Unicode font for PDF export.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 smmg \
 && mkdir -p /models /data/tmp_audio && chown -R smmg /models /data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY wsgi.py .
USER smmg
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4).status == 200 else 1)"

# ONE worker process on purpose: audio chunks sit on this container's disk and finalization is claimed in-process.
# Threads give the concurrency. --timeout is long because minutes generation can take a while.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "wsgi:app"]
