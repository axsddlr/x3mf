# syntax=docker/dockerfile:1

# x3mf is pure standard-library Python: no dependencies, no build step, no OS
# packages. The image is therefore just an interpreter plus the source, which
# is why there is no builder stage and no pip layer to cache or patch.
FROM python:3.12-alpine

LABEL org.opencontainers.image.title="x3mf" \
      org.opencontainers.image.description="Translate slicer 3MF projects between dialects: plates, parts, filaments, colours, layer ranges and process settings" \
      org.opencontainers.image.source="https://github.com/axsddlr/x3mf" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="0.1.0"

# Unbuffered logs, no .pyc written into a read-only root filesystem, and the
# package importable without installing it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Run as an unprivileged user from the first instruction that can. /data is the
# only path the server writes to (scratch files and the download store).
RUN addgroup -g 10001 -S x3mf \
 && adduser -u 10001 -S -G x3mf -h /app x3mf \
 && mkdir -p /data \
 && chown x3mf:x3mf /data

COPY --chown=x3mf:x3mf x3mf ./x3mf

# Only the two samples the demo uses; the repository holds others that are not
# part of the runtime image.
COPY --chown=x3mf:x3mf examples/foreign_broken.3mf examples/kit_ender.3mf ./examples/

USER x3mf:10001
EXPOSE 8791
VOLUME ["/data"]

# The health endpoint touches no disk and needs no shell or curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8791/api/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "x3mf"]
CMD ["web", "--host", "0.0.0.0", "--port", "8791", "--work-dir", "/data", "--demo", "--examples", "/app/examples"]
