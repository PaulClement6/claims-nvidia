# Agent image of the claim sandboxes (issue #14). Build it with infra/sandbox/setup.sh:
# the build context is made there (this file, requirements.txt and the Python files of
# backend/app that the graph needs, listed from its imports by
# backend/app/agent/sandbox_image.py). Never build it from the repository root: the
# root holds `.env`.
FROM python:3.12-slim

# iproute2: network namespace of the sandbox. nftables: bypass detection (OpenShell 0.1.5).
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 nftables \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1500 app \
    && useradd --uid 1500 --gid app --create-home app \
    && install -d -o app -g app /sandbox

# The sandbox has no route to PyPI: the packages are installed here.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app/app
WORKDIR /sandbox
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
# The supervisor replaces CMD. Docker uses this non-root USER for the agent processes.
USER app
CMD ["sleep", "infinity"]
