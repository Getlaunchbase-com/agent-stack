FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir fastapi uvicorn pydantic python-dotenv playwright==1.57.0

WORKDIR /app
COPY . /app

# Simple headless service (we'll script via router calling python in this container)
CMD ["bash", "-lc", "sleep infinity"]
