# ...existing code...
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Kerak bo'lsa C-kompilyator paketlari (pandas uchun)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Kodni konteynerga nusxalash
COPY . /app

# Python dependencylarni o'rnatish
RUN pip install --no-cache-dir -r requirements.txt

# Ishga tushirish
CMD ["python", "main.py"]
# ...existing code...