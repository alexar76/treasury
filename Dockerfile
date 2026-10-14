# MOMUS Treasury — the SEPARATE payer service. Built as its own image and run as its own
# container with its own key volume, so the payout key lives nowhere MOMUS can reach it.
# Build from the MONOREPO ROOT (needs oracle-core AND momus in context):
#     docker build -f treasury/Dockerfile -t momus-treasury .
FROM python:3.11-slim
WORKDIR /app

COPY oracles/core /app/core
COPY momus /app/momus
COPY treasury /app/treasury

RUN pip install --no-cache-dir -e "/app/core[pqc]" -e /app/momus -e /app/treasury

EXPOSE 9401
# Not root: the treasury (public API; its signing keys live in /data). uid 10001 owns only what it writes (/data).
RUN mkdir -p /data && chown -R 10001:10001 /data
ENV HOME=/tmp
USER 10001:10001

CMD ["python", "-m", "treasury.service"]
