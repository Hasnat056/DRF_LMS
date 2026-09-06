# The image, used by every stack: production, local development and test.
#
# The code is COPYed in and the image is self-contained -- nothing mounts a repo
# over /app except the test runner, which needs to re-run an edited suite
# without rebuilding. What differs between environments is how this image is
# run: which env file it reads (DJANGO_ENV), which services accompany it, and
# what is published. That is compose's job, not the image's.
#
# Rebuild after changing application code:
#     docker compose build backend                              # production
#     docker compose -f dev/docker-compose.yaml build backend   # local
#
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
RUN mkdir -p /app/staticfiles && chown -R 1000:1000 /app/staticfiles
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
#
# Everything the image should NOT carry -- .env, .git, .venv, generated
# benchmark output -- is excluded by .dockerignore. Without that this COPY
# bakes the real secrets into a shipped layer.
# -----------------------------
COPY --chown=drfuser:drfuser . /app/


USER drfuser


CMD ["gunicorn", "NexusAPI.wsgi:application", "--bind", "0.0.0.0:8000"]
