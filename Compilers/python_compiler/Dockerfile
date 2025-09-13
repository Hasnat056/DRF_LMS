# Base image with Python
FROM python:3.12-slim

# Environment
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1


# Working directory for code execution
WORKDIR /code

# Install FastAPI & Uvicorn
RUN pip install --no-cache-dir --default-timeout=180 fastapi uvicorn

# Copy the API code
COPY api.py /app/api.py

# Expose API port
EXPOSE 8000
# Run FastAPI server
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]




