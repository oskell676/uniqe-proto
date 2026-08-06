# UNIQE prototype — Python 3. Dependencies: pywebpush (Web Push notifications,
# needs proper elliptic-curve crypto that stdlib doesn't provide), httpx
# (HTTP/2 client, required by Apple's APNs provider API) and
# psycopg/psycopg_pool (Postgres storage layer).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY public/ ./public/

ENV HOST=0.0.0.0
ENV PORT=8080
ENV COOKIE_SECURE=1

EXPOSE 8080

CMD ["python3", "server.py"]
