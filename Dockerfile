FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# kerakli paketlar (xz-utils -> update-alternatives lzma ogohlantirishlarini kamaytiradi)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential xz-utils && \
    rm -rf /var/lib/apt/lists/*

# dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# kodni qo'shish va kerak bo'lsa huquqni sozlash
COPY . /app

# ixtiyoriy: non-root foydalanuvchi yaratish (agar xohlasangiz)
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]