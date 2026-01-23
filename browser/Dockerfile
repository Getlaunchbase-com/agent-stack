FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn pydantic python-dotenv

WORKDIR /app
COPY . /app

# Simple headless service (we'll script via router calling python in this container)
CMD ["bash", "-lc", "sleep infinity"]
