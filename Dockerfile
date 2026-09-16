# ==============================================================================
# Sentinel-Z Arena & Live Autonomous AI Web Agent
# Optimized Dockerfile for Render Free Tier / Cloud Deployments
# ==============================================================================

# Official Microsoft Playwright image with pre-installed Chromium & Linux GUI libraries
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Ensure non-buffered output, headless mode, and cloud defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    HEADLESS=true \
    HOST=0.0.0.0 \
    PORT=8801 \
    SENTINELZ_ROLE=victim

# Copy package requirements first for Docker layer caching
COPY pyproject.toml .

# Install dependencies including victim stack, browser-use, and langchain-openai
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .[victim] browser-use langchain-openai

# Copy codebase
COPY . .

# Expose default port (Render automatically maps $PORT to external HTTPS)
EXPOSE 8801

# Run Live Solo (Live Assistant Chat + Security Dashboard + Exfiltration Server on 1 container)
CMD ["python", "sz.py", "live-solo"]
