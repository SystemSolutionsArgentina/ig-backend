FROM python:3.11-slim

# ffmpeg es necesario para: convertir a MP3, y combinar video+audio en las
# calidades específicas (1080p/720p/480p). Sin esto, el backend solo podría
# ofrecer "La mejor" en formatos ya combinados (mp4 progresivo).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
