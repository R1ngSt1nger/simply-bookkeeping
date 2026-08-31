FROM python:3.12-slim

WORKDIR /app

# tesseract-ocr does the actual text recognition; poppler-utils (via pdf2image)
# renders PDF pages to images first, since Tesseract only reads image formats.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Persistent data lives here — mount a single volume onto this in docker-compose.
# The app creates its own db/ and attachments/ subfolders inside it.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
