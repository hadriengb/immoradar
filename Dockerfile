FROM python:3.12-slim
# poppler (pdftotext) et tesseract (OCR français) : lecture des PV et cahiers des conditions de vente
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV HOST=0.0.0.0 PORT=8765 IMMORADAR_DATA=/tmp/immoradar PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python3", "run.py"]
