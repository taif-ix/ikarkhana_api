FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYVISTA_OFF_SCREEN=true
ENV PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglx-mesa0 \
    libegl1 \
    libxrender1 \
    libxext6 \
    libsm6 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
