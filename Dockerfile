FROM python:3.9-slim

WORKDIR /app

# Install system dependencies for Playwright, image processing, etc.
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (needed for PDF generation)
RUN playwright install chromium

# Copy application code
COPY . .

# Expose port 5000 for the Flask app
EXPOSE 5000

# Run the Flask app
CMD ["python", "app.py"]
