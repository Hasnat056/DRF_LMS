# Use official Python slim base image
FROM python:3.12-slim

# -----------------------------
# Environment Variables
# -----------------------------
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# -----------------------------
# Create app user and group
# UID and GID are set to 1000 for consistency
# -----------------------------
RUN useradd -m -u 1000 drfuser

# -----------------------------
# Create /code directory with correct ownership
# This ensures named volume inherits correct permissions
# -----------------------------
RUN mkdir -p /code && chown -R 1000:1000 /code

# -----------------------------
# Set working directory for Django project
# -----------------------------
WORKDIR /app

# -----------------------------
# Install system dependencies
# -----------------------------
RUN apt-get update && apt-get install -y \
    gcc \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*


# -----------------------------
# Copy requirements first for better caching
# -----------------------------
COPY requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------
# Copy Django project files
# -----------------------------
COPY . /app/


USER drfuser


CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
