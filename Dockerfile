FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY gemini_stock /app/gemini_stock
COPY tests /app/tests

RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e '.[dev]'

CMD ["python", "-m", "gemini_stock.main"]
