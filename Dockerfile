# UNIQE prototype — Python 3, mostly stdlib. The one dependency is pywebpush
# (for Web Push notifications), which needs proper elliptic-curve crypto that
# stdlib doesn't provide.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY public/ ./public/

ENV HOST=0.0.0.0
ENV PORT=8080
ENV DATA_DIR=/data
ENV COOKIE_SECURE=1

EXPOSE 8080

CMD ["python3", "server.py"]
