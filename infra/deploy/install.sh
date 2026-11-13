#!/usr/bin/env bash
# Prepare one Ubuntu VM (22.04 or 24.04) for claims-nvidia: Docker, OpenShell 0.1.5 and
# its gateway, uv, Node 22, then the two services. Adapted from the NemoForge installer.
# Run as the normal user (not root). Uses sudo. Safe to run again.
#   infra/deploy/install.sh          install, build, start the services
#   infra/deploy/install.sh --check  Landlock check only (no change)
# After it: put NVIDIA_KEY in .env, run infra/sandbox/setup.sh, restart claims-backend.
# Every service listens on 127.0.0.1 only. See docs/DEPLOY.md.
set -euo pipefail

readonly OPENSHELL_VERSION="v0.1.5" # same pin as docs/DECISIONS.md
# sha256 of install.sh at tag v0.1.5. That installer checks the deb against the release checksums.
readonly INSTALLER_SHA256="7bf6706741b8b59c1b61e0c47030f0a34fefba6cee3c74cd52638cbf7cdc3655"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
readonly ROOT

die() {
  echo "install.sh: $*" >&2
  exit 1
}

# Landlock confines the files of the agent. No Landlock, no install.
grep -qs '^CONFIG_SECURITY_LANDLOCK=y' "/boot/config-$(uname -r)" || die "this kernel has no Landlock"
grep -qs landlock /sys/kernel/security/lsm || die "Landlock is built in but not active"
echo "Landlock: OK"
[[ "${1:-}" == "--check" ]] && exit 0
grep -qs '^ID=ubuntu' /etc/os-release || die "Ubuntu only (tested on 22.04)"
[[ "$(id -u)" != 0 ]] || die "run as the normal user, not root. The gateway is a user service."

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
command -v unzip >/dev/null || sudo apt-get install -y unzip

# The gateway and the app are systemd user services: keep them running without a login.
sudo loginctl enable-linger "$USER"
id -nG "$USER" | grep -qw docker || sudo usermod -aG docker "$USER"
# The user manager keeps the groups it started with. Without `docker`, the gateway
# cannot use the Docker socket: restart the manager.
docker_gid="$(getent group docker | cut -d: -f3)"
manager_pid="$(systemctl show "user@$(id -u).service" -P MainPID)"
if ! grep -Eq "^Groups:.*\b${docker_gid}\b" "/proc/$manager_pid/status"; then
  sudo systemctl restart "user@$(id -u).service"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Write the gateway config before the first start: without a driver it does not start.
config="$HOME/.config/openshell/gateway.toml"
if [[ ! -f "$config" ]]; then
  mkdir -p "$(dirname "$config")"
  cat >"$config" <<'TOML'
[openshell]
version = 2

[openshell.gateway]
compute_driver = "docker"

[openshell.drivers.docker]
socket_path = "/var/run/docker.sock"
TOML
fi

if [[ "$(openshell --version 2>/dev/null | awk '{print $2}')" != "${OPENSHELL_VERSION#v}" ]]; then
  installer="$(mktemp)"
  curl -fsSL "https://raw.githubusercontent.com/NVIDIA/OpenShell/${OPENSHELL_VERSION}/install.sh" -o "$installer"
  echo "$INSTALLER_SHA256  $installer" | sha256sum -c --quiet - ||
    die "checksum mismatch for the OpenShell ${OPENSHELL_VERSION} installer. Not run."
  env OPENSHELL_VERSION="$OPENSHELL_VERSION" sh "$installer"
  rm -f "$installer"
fi
systemctl --user enable openshell-gateway
systemctl --user restart openshell-gateway
for _ in {1..30}; do
  openshell status 2>/dev/null | grep -q Connected && break
  sleep 2
done
openshell status | grep -q Connected || die "the gateway is not ready. See: journalctl --user -u openshell-gateway"

# uv and Node 22 (NodeSource), then the dependencies and the built frontend.
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
if ! node --version 2>/dev/null | grep -q '^v22'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
(cd "$ROOT/backend" && uv sync)
(cd "$ROOT/frontend" && npm ci && npm run build)

# .env on the VM only, mode 600. sandbox mode, on 127.0.0.1:8000.
if [[ ! -f "$ROOT/.env" ]]; then
  (umask 077 && sed 's/^CLAIMS_MODE=.*/CLAIMS_MODE=sandbox/' "$ROOT/.env.example" >"$ROOT/.env")
fi
chmod 600 "$ROOT/.env"

units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat >"$units/claims-backend.service" <<UNIT
[Unit]
Description=claims-nvidia backend (FastAPI, 127.0.0.1:8000)
After=openshell-gateway.service

[Service]
WorkingDirectory=$ROOT/backend
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$HOME/.local/bin/uv run uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
cat >"$units/claims-frontend.service" <<UNIT
[Unit]
Description=claims-nvidia frontend (built files, 127.0.0.1:5173, proxy to the backend)

[Service]
WorkingDirectory=$ROOT/frontend
ExecStart=/usr/bin/npm run preview -- --host 127.0.0.1 --port 5173 --strictPort
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable claims-backend claims-frontend
systemctl --user restart claims-backend claims-frontend

echo "Done. OpenShell $(openshell --version). Next: docs/DEPLOY.md, step 3 (the key and the sandbox image)."
id -nG | grep -qw docker || echo "IMPORTANT: this login has no docker group yet. Log out and log in again."
