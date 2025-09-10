# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y gcc libpq-dev

# Copy only requirements first for caching
COPY requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /app/

# Default command is just a placeholder; we'll override in Compose
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

RUN useradd -m celeryuser
USER celeryuser

