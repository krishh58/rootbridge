FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# WeasyPrint runtime dependencies (PDF export)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi8 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn "app:create_app()" --bind "0.0.0.0:$PORT" --workers 2 --timeout 60
