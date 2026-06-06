FROM python:3.11-slim

# WeasyPrint dependencies (PDF export)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 libffi-dev libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright installs Chromium and all its own system dependencies via --with-deps
RUN playwright install chromium --with-deps 2>/dev/null || true

COPY . .

CMD gunicorn "app:create_app()" --bind "0.0.0.0:$PORT" --workers 2 --timeout 60
