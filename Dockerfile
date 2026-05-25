FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Default: run the API
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
