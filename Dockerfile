FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY karvon ./karvon
RUN pip install --no-cache-dir ".[postgres,scale]"

RUN useradd --create-home karvon
USER karvon
# Without KARVON_DATABASE_URL (e.g. plain `docker run`), keep SQLite in a writable place.
ENV KARVON_DATABASE_URL=sqlite:////home/karvon/karvon.db

EXPOSE 8000
# --proxy-headers: behind an HTTPS proxy, mark the login cookie as Secure.
CMD ["uvicorn", "karvon.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
