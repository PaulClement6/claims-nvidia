#!/usr/bin/env bash
# One-time setup of `CLAIMS_MODE=sandbox` (issue #14). Run it from anywhere:
#
#   infra/sandbox/setup.sh
#
# It does three things on this machine, and nothing else:
#   1. builds the agent image `claims-agent:1` on the Docker engine of the OpenShell
#      gateway (Colima on a Mac). The build context is made by
#      backend/app/agent/sandbox_image.py: this folder's Dockerfile and requirements,
#      and the Python files of the graph. It never holds `.env`.
#   2. imports the provider profile `claims-nim` (infra/sandbox/profile-nim.yaml).
#   3. creates the provider `claims-nim` with the key from NVIDIA_KEY. The key goes in
#      the environment of that one command: never an argument, never a file, never
#      printed. The sandboxes see a placeholder; OpenShell swaps in the key on the way
#      to integrate.api.nvidia.com only.
#
# Every name starts with `claims-`. It touches no sandbox, provider, profile or image
# of another project (NemoForge has its own on the same gateway).
# Needs: OpenShell 0.1.5 with a connected gateway, Docker (Colima), uv, and NVIDIA_KEY
# in $ENV_FILE (default: .env at the repository root).
# Safe to run again: it replaces the profile and the provider of this app. It stops if
# a sandbox of this app still exists (empty it with the demo reset first).
set -u # no pipefail: grep -q closes pipes early

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly repo="$here/../.."
readonly image="${CLAIMS_SANDBOX_IMAGE:-claims-agent:1}" provider="claims-nim" profile="claims-nim"
readonly env_file="${ENV_FILE:-$repo/.env}"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/default/docker.sock}"

die() {
  echo "setup: $*" >&2
  exit 2
}

echo "== Prerequisites"
command -v openshell >/dev/null || die "the openshell command is missing"
[[ "$(openshell --version | awk '{print $2}')" == "0.1.5" ]] || die "OpenShell 0.1.5 is required"
openshell status 2>&1 | grep -q Connected || die "no OpenShell gateway is connected (is Colima running?)"
command -v docker >/dev/null || die "the docker command is missing"
docker info >/dev/null 2>&1 || die "the Docker engine does not answer on $DOCKER_HOST"
command -v uv >/dev/null || die "uv is missing"
grep -q '^NVIDIA_KEY=.' "$env_file" || die "NVIDIA_KEY is missing in $env_file"
if openshell sandbox list --names 2>/dev/null | grep -q '^claims-'; then
  die "a sandbox of this app exists: reset the demo (POST /demo/reset) to delete it, then run again"
fi
echo "ok: OpenShell 0.1.5, gateway connected, Docker on $DOCKER_HOST"

echo "== Image $image"
ctx="$(mktemp -d)"
trap 'rm -rf "$ctx"' EXIT
uv run --no-project --python 3.12 python "$repo/backend/app/agent/sandbox_image.py" "$ctx" ||
  die "could not make the build context"
echo "build context: $(find "$ctx" -type f | wc -l | tr -d ' ') files, none of them a .env"
docker build -q -t "$image" "$ctx" >/dev/null || die "docker build failed"
echo "built $image"

echo "== Provider profile and provider $provider"
openshell provider delete "$provider" >/dev/null 2>&1
openshell profile delete "$profile" >/dev/null 2>&1
openshell profile import -f "$here/profile-nim.yaml" >/dev/null || die "profile import failed"
# One variable of this process, in the environment of one command. Never printed,
# never an argument, never written.
key="$(grep '^NVIDIA_KEY=' "$env_file" | head -1 | cut -d= -f2- | tr -d "\"'")"
NVIDIA_API_KEY="$key" openshell provider create --name "$provider" --type "$profile" \
  --credential NVIDIA_API_KEY >/dev/null || die "provider create failed"
unset key
echo "created provider $provider"

echo "== Check"
openshell provider get "$provider" >/dev/null || die "the provider is not there"
docker image inspect "$image" >/dev/null || die "the image is not there"
echo "Ready. Start the backend with CLAIMS_MODE=sandbox (see docs/ARCHITECTURE.md section 7)."
