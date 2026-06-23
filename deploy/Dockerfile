# Reusable for the whole service regardless of how many dashboards are registered -
# adding dashboard #2, #3, ... never requires touching this file.

FROM artifactory.fanniemae.com/gbx-coss-release/ecos-base-amzlnx-python311:latest

WORKDIR /app
COPY backend/requirements.txt .
RUN /usr/bin/pip3.11 install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV RUNTIME_ENV=production
ENV DATA_LAYER=athena
ENV PORT=8000
EXPOSE 8000

# BuildKit-only syntax (e.g. --mount=type=cache) intentionally avoided - BuildKit
# is disabled on the corporate network. Build with:
#   $env:DOCKER_BUILDKIT=0   (PowerShell)
#   docker build -t fnma-reporting-backend:latest -f deploy/Dockerfile ..

CMD ["/usr/bin/python3.11", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
