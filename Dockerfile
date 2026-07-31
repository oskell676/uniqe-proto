# UNIQE prototype — stdlib-only Python, so no pip install step is needed.
FROM python:3.12-slim

WORKDIR /app

COPY server.py .
COPY public/ ./public/

ENV HOST=0.0.0.0
ENV PORT=8080
ENV DATA_DIR=/data
ENV COOKIE_SECURE=1

EXPOSE 8080

CMD ["python3", "server.py"]
