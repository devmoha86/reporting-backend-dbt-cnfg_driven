# Reusable for the whole service regardless of how many dashboards are registered -
# adding dashboard #2, #3, ... never requires touching this file.

FROM python:3.11-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV RUNTIME_ENV=production
ENV DATA_LAYER=athena
ENV PORT=8000
EXPOSE 8000

# BuildKit-only syntax (e.g. --mount=type=cache) intentionally avoided - BuildKit
# is disabled on the corporate network. Build with:
#   $env:DOCKER_BUILDKIT=0   (PowerShell)
#   docker build -t fnma-reporting-backend:latest -f deploy/Dockerfile ..

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
