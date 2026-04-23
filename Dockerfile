# Use lightweight official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Prevent Python from creating .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Back4App expects port 8000
EXPOSE 8000

# Run the app
# Change 'server:app' to 'your_file_name:app_instance' if needed
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]