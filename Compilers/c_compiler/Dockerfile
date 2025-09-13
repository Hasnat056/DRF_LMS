# Base image
FROM ubuntu:22.04


# Working directory for code execution
WORKDIR /code

# Install build tools and Python for FastAPI
RUN apt-get update && \
    apt-get install -y g++ gcc python3 python3-pip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install FastAPI & Uvicorn
RUN pip3 install --no-cache-dir --default-timeout=180 fastapi uvicorn

# Copy API code
COPY api.py /app/api.py

# Expose API port
EXPOSE 8000

# Run FastAPI server
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]




