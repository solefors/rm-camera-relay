FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends aapt apksigner && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir aiohttp==3.12.15
COPY service ./service
CMD ["python", "-m", "service.app"]
