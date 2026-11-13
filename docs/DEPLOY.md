# Deploy on the Brev VM

The demo runs on one CPU VM, in `sandbox` mode, with the hosted NVIDIA endpoints. No GPU.

Deployed and tested on 2026-11-13 (issue #16).

## The VM

| | |
|---|---|
| Name | `claims-nvidia` (Brev organisation of Paul) |
| Type | `n2d-standard-4` (GCP): 4 vCPU, 16 GB, 125 GB disk, about $0.13 per hour |
| System | Ubuntu 22.04, kernel 6.8, Landlock active |
| Code | `~/claims-nvidia`, branch `main`, user `ubuntu` |

Nothing is open to the Internet. SSH is closed from outside (checked): access goes through Brev only. Every service listens on `127.0.0.1`.

| Service (systemd user unit) | Listens on |
|---|---|
| `claims-frontend` (the built frontend, `vite preview`, proxy to the backend) | `127.0.0.1:5173` |
| `claims-backend` (FastAPI, `CLAIMS_MODE=sandbox`) | `127.0.0.1:8000` |
| `openshell-gateway` (OpenShell 0.1.5, Docker driver) | `127.0.0.1:17670` |

The three units start at boot (linger is on). Docker Compose is not used on the VM: in `sandbox` mode the backend calls the `openshell` command of the host.

## Open the demo from your machine

You need the Brev CLI and a login (`brev login`). Then:

```sh
brev port-forward claims-nvidia -p 5173:5173
```

Open `http://localhost:5173`. The sidebar must show `Mode: sandbox`.

## Install from zero (about 5 minutes)

On a fresh Ubuntu VM, as the normal user:

```sh
git clone https://github.com/PaulClement6/claims-nvidia.git ~/claims-nvidia
cd ~/claims-nvidia
infra/deploy/install.sh
```

1. `install.sh` checks Landlock, then installs Docker (if missing), OpenShell 0.1.5 (the installer is checked against a recorded sha256), uv and Node 22. It writes the gateway config before the first start of the gateway. It builds the frontend, writes `.env` from `.env.example` (mode 600, `CLAIMS_MODE=sandbox`) and starts the two units. You can run it again.
2. Put the NVIDIA key in `.env` with an editor (`nano .env`, line `NVIDIA_KEY=`). Never `echo` the key: it would stay in the shell history. The backend does not start in `sandbox` mode before step 3.
3. Build the agent image and create the provider, then start the backend:

```sh
infra/sandbox/setup.sh
systemctl --user restart claims-backend
curl -s localhost:8000/health
```

The answer must hold `"mode":"sandbox"`.

## Update after a merge on `main`

```sh
cd ~/claims-nvidia && git pull
(cd backend && uv sync) && (cd frontend && npm ci && npm run build)
curl -s -X POST localhost:8000/demo/reset   # deletes the claims, the uploads and the sandboxes
infra/sandbox/setup.sh                      # only after a change of the graph files
systemctl --user restart claims-backend claims-frontend
```

## Check

```sh
systemctl --user status claims-backend claims-frontend openshell-gateway
journalctl --user -u claims-backend -n 50
cd ~/claims-nvidia/backend && set -a && . ../.env && set +a
systemctl --user stop claims-backend      # the tests use port 8000
uv run pytest -m "sandbox or live"        # 9 tests, about 3 minutes, real model and real sandboxes
systemctl --user start claims-backend
```

Result on 2026-11-13: 9 of 9 pass. In the browser: claim file 3 gives `investigation` (130 points, 7 signals), and the confinement test gives 5 denials of 5.

## For the intern

1. Paul sends you an invite link to his Brev organisation (`brev invite`) and gives you access to the VM.
2. On your machine: install the Brev CLI, `brev login`, then `brev shell claims-nvidia` (a shell) or `brev open claims-nvidia code` (VS Code on the VM, with Copilot).
3. Do not work in `~/claims-nvidia`: that folder is the demo. Work in your own copy, on other ports:

```sh
git clone https://github.com/PaulClement6/claims-nvidia.git ~/work/claims-nvidia
cd ~/work/claims-nvidia/backend && uv sync && uv run uvicorn app.main:app --port 8001
cd ~/work/claims-nvidia/frontend && npm ci && BACKEND_URL=http://localhost:8001 npm run dev -- --port 5174
```

From your machine: `brev port-forward claims-nvidia -p 5174:5174`.

4. For the Omni reader (task 26) you need a key: copy `.env.example` to `.env` in your copy and ask Paul for the key. Never commit `.env`.

## Costs and stop

The VM costs about $3 per day when it runs. Stop it when nobody uses it: `brev stop claims-nvidia`, then `brev start claims-nvidia`. Not yet tested: the state of the services after a stop and a start.

## Limits

- No access control on the app (D33). It is safe only because nothing is public. Add a login before you open a port.
- One free key for the VM: about 40 requests per minute, so about 2 claims per minute.
- Tested with one visitor at a time. Not tested: several sandboxes at the same time.
