FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/workspace

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy root requirements (all services share the core package set for simple local runs)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared code first
COPY shared ./shared

# Copy service specific code
COPY api-service/app ./app

EXPOSE 8000

CMD ["python", "-m", "app.api.main"]
