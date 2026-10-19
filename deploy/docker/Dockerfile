# ai-quotas sampler (and, with EXTRAS=plot, a hub). docs/SAMPLERS.md
#
#   docker build -f deploy/docker/Dockerfile -t ai-quotas-sampler .
#   docker build -f deploy/docker/Dockerfile --build-arg EXTRAS=plot -t ai-quotas-hub .
#
# No vendor CLI inside: logins are made once on a machine with a browser
# (`ai-quotas login … --move-to`) and mounted in. The runtime is stdlib only.
FROM python:3.13-slim

ARG EXTRAS=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates rsync openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY ai_quotas ./ai_quotas
RUN pip install --no-cache-dir ".${EXTRAS:+[$EXTRAS]}" \
 && rm -rf /src \
 && useradd --uid 10001 --user-group --create-home --home-dir /home/sampler sampler \
 && mkdir -p /data/logins /data/spool \
 && chown -R 10001:10001 /data

USER 10001:10001
WORKDIR /data
ENV PYTHONUNBUFFERED=1 \
    AI_QUOTAS_ROLE=sampler \
    AI_QUOTAS_DATA_DIR=/data \
    AI_QUOTAS_LOGINS_DIR=/data/logins \
    AI_QUOTAS_SPOOL_DIR=/data/spool \
    AI_QUOTAS_READ_DOTENV=0

ENTRYPOINT ["ai-quotas", "sampler"]
CMD ["--all", "--once"]
