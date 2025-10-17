FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# install system deps; man-db qo'shilsa update-alternatives ogohlantirishlari kamayadi
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential xz-utils man-db && \
    rm -rf /var/lib/apt/lists/*

# dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# kodni qo'shish va huquqni sozlash
COPY . /app

# non-root user yaratish va ishlatish
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]