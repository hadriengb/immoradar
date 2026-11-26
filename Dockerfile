FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV HOST=0.0.0.0 PORT=8765 IMMORADAR_DATA=/tmp/immoradar PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python3", "run.py"]
