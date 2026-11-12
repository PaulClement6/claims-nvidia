# Agent image of the spike (issue #13). Build with the Docker engine of the gateway
# (Colima): `docker build -t claims-spike-agent:1 infra/spike`.
FROM python:3.12-slim

# iproute2: network namespace of the sandbox. nftables: bypass detection. curl: spike probes.
RUN apt-get update && apt-get install -y --no-install-recommends curl iproute2 nftables \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1500 app \
    && useradd --uid 1500 --gid app --create-home app \
    && install -d -o app -g app /sandbox

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY agent /app/agent
WORKDIR /sandbox
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
# The supervisor replaces CMD. Docker uses this non-root USER for the agent processes.
USER app
CMD ["sleep", "infinity"]
