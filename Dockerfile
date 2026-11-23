FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY karvon ./karvon
RUN pip install --no-cache-dir ".[postgres]"

RUN useradd --create-home karvon
USER karvon

EXPOSE 8000
CMD ["uvicorn", "karvon.main:app", "--host", "0.0.0.0", "--port", "8000"]
