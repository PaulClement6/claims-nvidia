# Decisions

## Taken (2026-11-12, interview with Paul)

| # | Decision |
|---|---|
| D1 | Three claim types: property / household, health / accident, travel. |
| D2 | Routing: type first (one workflow per type), then risk (three outcomes). |
| D3 | Fraud signals of the first version: rule-based red flags, document tampering, cross-document consistency. Duplicate detection comes later. |
| D4 | The customer deposits the claim, through a chat with a claim agent. |
| D5 | UI with two panels: customer panel (agent only) and pipeline panel (all the plumbing). |
| D6 | The agent runs in an OpenShell sandbox (NemoClaw / OpenShell). It calls document intelligence tools and workflows. |
| D7 | No automatic rejection. A flagged customer sees "escalated to a specialist". The back end shows the fraud flag and a report with the reasons. |
| D8 | Demo login: the customer picks a mock account, then types or searches a policy of that account. |
| D9 | English only. |
| D10 | Mock documents that look real. No real claim data. |
| D11 | Backend FastAPI. Stack close to NemoForge. Demo on a Brev instance. |
| D12 | Replaced by D22. |
| D13 | First define what we build. Then choose the features the intern builds and improves. |

## Taken (2026-11-12, grill session)

| # | Decision | Closes |
|---|---|---|
| D14 | At the event, visitors use the demo themselves. They do not upload their own documents. We give a kit of 20 to 30 mock documents for the three mock accounts: clean, scanned, photographed and tampered. A visitor picks an account, tells any story and mixes the documents. The screen says "do not upload a real document". The reset deletes the uploaded files. | |
| D15 | Version 1 reads documents with one vision-language model alone: Nemotron 3 Nano Omni (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`). It gets the page image and fills the claim JSON. We call the hosted NVIDIA endpoint with a free developer key. This is acceptable because the data is mock data. Nemotron Nano 12B v2 VL is out: it has no hosted endpoint today. | O1, O4 |
| D16 | The specialised chain (Nemotron Parse, OCR v2, page elements, table structure) comes after version 1, and only for the cases where the measures show that Omni fails. These are intern tickets. The chain needs GPU budget that we do not have yet. | O4 |
| D17 | Agent model: a one-day spike tests Omni in this role, so one model does both jobs. If it fails, the agent uses `nvidia/nemotron-3.5-lightning-30b-a3b`, which NemoForge already uses. | O3 |
| D18 | The customer sees the same exact message for `handler_review` and `investigation`. Only `straight_through` has a different message. A test compares the two texts. Reason: a different message tells a fraudster that the system flagged the claim. | |
| D19 | The agent reaches the tools through one MCP server outside the sandbox. The sandbox network policy allows it. Same pattern as NemoForge. | O5 |
| D20 | No Temporal. Plain Python and SQLite. | O7 |
| D21 | Stand-alone project. The tool gateway and the workflows hold no UI code, so they can move to NemoForge later. | O8 |
| D22 | Repository `PaulClement6/claims-nvidia`. Public, so the intern can work on it and deploy it on a VM. | O10, D12 |
| D23 | The intern is a data engineer. He works with GitHub Copilot, for two to three weeks, starting now. He owns the document reading pipeline: six guided tasks, each with things to test and parameters to tune (`docs/BACKLOG.md`, M4). The README tells him what is his. | O11 |
| D24 | Boundary between the base and the intern: the tool `extract_document` has a fixed contract (a document in, fields with their sources out). In the base a **fixture reader** returns the expected fields of each kit document from its `expected.json`. The full flow works with no model. The intern puts the real reader behind the same contract. The fixture reader replaces the "text layer of the PDF" reader. | |
| D25 | The OpenShell sandbox is part of the base, not a later step. It runs on the Brev VM, and the intern has access to this VM. `local` mode still works with no sandbox and no GPU. | |
| D26 | The UI follows the look of NemoForge. | |

## Taken by default (Paul did not answer; he can change them)

| # | Decision | Closes |
|---|---|---|
| D27 | Agent harness: Hermes, driven without a terminal through its API server. A spike confirms it. Start from the OpenShell 0.1.5 setup that NemoForge proved, then test 0.1.8 (breaking changes: the gateway refuses to start without authentication, and static credentials go only in the `Authorization` header). | O2 |
| D28 | NeMo Guardrails in front of the agent model: after the first demo. | O6 |
| D29 | The pipeline panel is read-only in version 1. Confirm and correct per field is a later ticket. | O9 |

## Taken while we built the base (2026-11-12)

| # | Decision |
|---|---|
| D30 | The agent runs the checks only when the customer says "done" (or presses Done). When the required documents are all present it asks for more documents first. Reason: a visitor can add a second photo or any other document of the kit. |
| D31 | The agent gets only the customer text from `assess_and_route`. It never sees the outcome, the score or the reasons. Trusted code writes the fraud report when the outcome is `investigation`. Reason: the next agent is an LLM, and it must not be able to tell the outcome or to skip the report. |
| D32 | The outcome `investigation` and its fraud report are saved in one write. The store refuses an `investigation` with no report (non-negotiable 4). |
| D33 | Two API views: `GET /claims/{id}/customer` for the customer panel, `GET /claims/{id}` and `/events` for the pipeline panel. There is no access control between them: both panels are on one screen in the demo. Add it before the customer panel is served alone. |
| D34 | Thresholds, each one named constant: low confidence below 0.8; R003 window 0 to 14 days; T003 tolerance CHF 0.05. The points per signal are one table in `backend/app/checks/rules.py`. |
| D35 | The fixture reader finds a mock document by the SHA-256 of its bytes. The mock PDFs are byte-identical on every run of the generator. The damage photos are simple drawings: real-looking photos come with the document kit (issue #23). |

## Open

| # | Question | Proposal |
|---|---|---|
| O12 | Are the documents, fields and limits of `docs/WORKFLOWS.md` right for the claim types we want to show? | Paul confirms or corrects. The base uses them as written. |
| O13 | Which GPU on Brev for a self-hosted demo? | Not needed for version 1: the models are hosted endpoints, so the VM needs no GPU. For a self-hosted demo: Omni needs 80 GB (one A100, about USD 1.66 to 1.98 per hour). The memory of Parse and OCR v2 is not in the NVIDIA docs: measure it. |
| O14 | What is the date of the event? | Needed to plan the dry run. |
| O15 | What is the rate limit of the free NVIDIA developer key? | Measure it in the first intern task. |

# Spike: LangGraph agent in OpenShell (issue #13)

Date: 2026-11-12

Issue: [#13](https://github.com/PaulClement6/claims-nvidia/issues/13)

Command: `ENV_FILE=<path of .env> infra/spike/run-spike.sh` (add `--keep` to keep the sandbox). Files: `infra/spike/`.

Scope change: the brief said Hermes. The product owner then chose our own agent, built with LangGraph and LangChain. This spike tests that agent. Decision D27 (Hermes through its API server) is replaced by decision D36 below. Hermes was not run. The only Hermes fact here comes from `hermes --help` in the NemoForge image: it has `-z` and `chat -q` for one-shot use.

## Result

The spike passes. All 21 checks print PASS on one clean run (the first run had 1 FAIL, see "What did not work first"). A LangGraph agent runs in an OpenShell sandbox. The host sends turns with `openshell sandbox exec`. The agent calls the model through the OpenShell provider. It calls one tool on an MCP server outside the sandbox. A second tool on the same server is denied.

## Decision

| # | Decision |
|---|---|
| D36 | The claim agent is a LangGraph agent (`langchain.agents.create_agent`) with a SQLite checkpointer. It runs in an OpenShell sandbox. The backend sends each turn with `openshell sandbox exec` and reads one JSON line. One sandbox per claim. The thread id is the claim id. Replaces D27. |

## Tested versions

| Component | Version |
| --- | --- |
| macOS, Apple arm64 | 26.6.2 |
| Colima | 0.10.3, Docker runtime, Docker 29.5.2, kernel 6.8.0-117-generic |
| OpenShell CLI and gateway | 0.1.5 (mTLS), Docker compute driver |
| Agent image | `python:3.12-slim` (Debian 13.7, Python 3.12.16), 429 MB |
| langgraph | 1.2.21 |
| langchain | 1.5.0 (`create_agent`) |
| langchain-openai | 1.7.4 (`ChatOpenAI`, uses httpx 0.28.1) |
| langchain-mcp-adapters | 0.3.2 (needs `mcp<2`) |
| langgraph-checkpoint-sqlite | 3.1.3 |
| mcp (client in the image, server on the host) | 1.30.0 |
| Model | `nvidia/nemotron-3.5-lightning-30b-a3b` on `https://integrate.api.nvidia.com/v1`, no Guardrails proxy |

The pins are in `infra/spike/requirements.txt`. `pip install mcp` gives version 2.3.0 today. In version 2 `FastMCP` is renamed. Pin version 1.30.0.

## What worked

- Image: `python:3.12-slim`, `iproute2`, `nftables`, `curl`, user `app` (uid 1500), `/sandbox` as workspace, packages installed at build time. `openshell sandbox create --from claims-spike-agent:1` uses the image from the local Docker engine of the gateway. The image `CMD` is replaced: pass `-- sleep infinity`.
- Model: `ChatOpenAI(base_url=..., api_key=os.environ["NVIDIA_API_KEY"])`. The variable holds the placeholder `openshell:resolve:env:v...`. The supervisor swaps in the real key on the `Authorization` header toward `integrate.api.nvidia.com` only. The call worked with httpx. The sandbox sets `SSL_CERT_FILE` to the CA of the supervisor, and httpx reads it.
- MCP: `MultiServerMCPClient` with `transport: streamable_http` works against a FastMCP server on the host (`uv run infra/spike/mcp_server.py`). The sandbox reaches it as `host.openshell.internal`.
- Memory: `AsyncSqliteSaver` in `/sandbox/state/checkpoints.sqlite`. Each turn is a new process. The thread id keeps the conversation. Another thread has no memory of it.

## What did not work first

- The image `ENV` is not passed to `sandbox exec`. `python -m agent` failed with "No module named agent". Fix: `--env PYTHONPATH=/app` on `sandbox create`.
- The FastMCP server answered 421 to the Host header `host.openshell.internal:18091`. Fix: list that host in `allowed_hosts` of `TransportSecuritySettings`.
- The model wrote `POL‑7731‑AUTO` with a non-breaking hyphen (U+2011) in 1 of 3 runs of the script. A check that compares text failed. The system prompt now asks for plain ASCII. See `docs/LESSONS.md`.
- `set -o pipefail` with `grep -q` made the script say the gateway was not connected. The script does not use `pipefail`.

## Question A: how does the host send a turn?

Answer: `openshell sandbox exec`. The message goes on stdin, the answer is the last JSON line on stdout.

```sh
printf '%s' "$MESSAGE" | openshell sandbox exec --name claims-spike-1 --no-tty --no-login-shell -- \
  python -m agent --thread CLAIM-1
# {"answer": "...", "tools": [{"name": "get_customer_policies", "args": {...}}], "messages": 4, "seconds": 3.4}
```

- Stdin avoids the quoting problem of `--message` through the shell.
- Time per turn, measured on the host (12 turns): 4.3 to 4.9 s for a turn with one tool call. 1.8 to 4.1 s for a turn with no tool call. The model call is most of it.
- The fixed cost of `exec` is 0.08 s. Importing the Python packages costs 0.74 s.
- Turn 2 on the same thread answered "POL-7731-AUTO" with no tool call and no customer id in the question. A new thread asked the customer id.
- Not tested: an HTTP server in the sandbox behind `openshell forward`. It saves about 1 s per turn (imports) and would keep the MCP session open. Add it only if the demo needs it. `exec` also streams stdout, so JSON lines per event are possible. Not tested.
- Hermes candidates (API server, ACP) were not tested.

## Question B: MCP

Answer: pass. The prompt was "Which policies does customer C-1001 have?". The tool is not named.

```text
{"answer": "Customer C-1001 has two policies: POL-7731-AUTO (Motor, limit 25,000 CHF), POL-7732-HOME (Household, 12,000 CHF)",
 "tools": [{"name": "get_customer_policies", "args": {"customer_id": "C-1001"}}], ...}
host mcp.log: Processing request of type CallToolRequest
```

The agent code binds only the tools in `AGENT_TOOLS` (default: `get_customer_policies`). `tools/list` returns both tools: OpenShell does not filter the list. The agent filters it, the policy enforces it.

Policy (`infra/spike/policy.yaml`), network part:

```yaml
claims_gateway:
  endpoints:
    - host: host.openshell.internal
      port: 18091
      path: /mcp
      protocol: mcp
      enforcement: enforce
      rules:
        - allow: {method: initialize}
        - allow: {method: notifications/initialized}
        - allow: {method: tools/list}
        - allow: {method: tools/call, tool: get_customer_policies}
  binaries: [/usr/local/bin/python3.12, /usr/local/bin/python3, /usr/local/bin/python, /usr/bin/curl]
```

- The `langchain-mcp-adapters` client sends 4 methods: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`. It makes a new session for each tool call (initialize, initialized, tools/call, tools/list). In 4 turns: 4 of each, 1 call. The log shows no denial, so `ping` is not needed. This client does not send `ping`. Hermes did.
- The server runs in stateless mode (`stateless_http=True`). It gives no session id, so the client sends no `DELETE`. A stateful server will give a session id. The client then closes the session with `DELETE`. The NemoForge notes (issue #90) say the policy denies a `DELETE` (no JSON body). Test this with the real tool gateway (ticket for the gateway).
- Protocol revision: the client and the server agreed on a revision that OpenShell accepts with no `mcp.versions` setting (default `2025-11-25`). No `protocol version` denial in the log. I did not log the number itself.
- Profile (`infra/spike/profile-nim.yaml`): endpoint `integrate.api.nvidia.com:443`, `protocol: rest`, rule `POST /v1/chat/completions` only. A `GET /v1/models` from the sandbox gets 403 (check C1b). The profile has no endpoint for the MCP server, so the policy owns the MCP rules.
- The MCP server has no authentication in the spike. The real tool gateway needs it. NemoForge solved this with a second provider and `credential_binding` (its decisions, issue #62).

## Question C: confinement

All checks pass. Evidence, shortened:

| Check | Evidence |
| --- | --- |
| C1 other host | `curl https://example.com` gives code 000. `httpx` to `api.github.com` gives `ConnectError: [Errno 13] Permission denied`. |
| C1b model host, other path | `GET https://integrate.api.nvidia.com/v1/models` gives 403. |
| C2 other tool, same server | `tools/call get_customer_notes` gives 403 `policy_denied`. The note text never came back. The allowed tool still works with `curl`. |
| C3 key | `env \| grep -i nvidia` shows only `NVIDIA_API_KEY=openshell:resolve:env:v..._NVIDIA_API_KEY`. The real value is absent from the environment, `/proc/self/environ`, `/sandbox`, `/tmp`, `/etc`, `/app`, `/home`, `/run` and the image. The check reads the value on stdin, so it is never in a command line. |
| C4 host folders | No `/Users`, `/host_mnt` or `/Volumes`. No `virtiofs`, `9p` or `fuse` mount. `/sandbox` is writable. `/app` is read-only. |

Denial log lines (`openshell logs claims-spike-1 --source sandbox`):

```text
NET:REFUSE [MED] DENIED example.com [reason:policy_dns_ineligible]
NET:OPEN [MED] DENIED /usr/bin/curl(0) -> example.com:443 [reason:transparent_tcp_policy_denied]
NET:OPEN [MED] DENIED /usr/local/bin/python3.12(0) -> api.github.com:443 [reason:transparent_tcp_policy_denied]
HTTP:GET [MED] DENIED GET https://integrate.api.nvidia.com/v1/models [policy:_provider_claims_spike_nim engine:l7] [reason:L7_REQUEST deny GET integrate.api.n...]
HTTP:POST [MED] DENIED POST http://host.openshell.internal:18091/mcp [policy:claims_gateway engine:l7-mcp] [reason:JSONRPC_L7_REQUEST decision=deny rule_methods=tools/call tools=get_customer_notes http_method=POST endpoint=host.openshell.internal:18091/mcp policy_version=1 reason=MCP tool call has no matching allow rule; ...]
```

Limits of this proof, with no weakening of the policy to pass a check:

- `/usr/bin/curl` is in the binaries for the probes. Remove it in the product image policy. OpenShell matches a binary by process ancestry, so a tool that can start a shell can also reach the endpoints. The agent has no shell tool, but the L7 rules (method and tool) are the guarantee, not the binary list.
- I did not test the swap toward a wrong host (the key sent to the MCP server). The NemoForge notes say it happens only toward the profile endpoint.
- The Colima VM mounts `/Users` from the Mac. The sandbox container does not see it. The check is on the sandbox only.

## Question D: what the backend does

Once per deployment:

1. Build the image on the Docker host of the gateway: `docker build -t claims-agent:1 infra/spike`. Packages are baked in, because the sandbox has no route to PyPI. Confirmed: the policy allows no PyPI host, and the image has all packages.
2. `openshell profile import -f profile-nim.yaml`.
3. `NVIDIA_API_KEY=<value from .env> openshell provider create --name claims-nim --type claims-spike-nim --credential NVIDIA_API_KEY`. The value is in the process environment only.
4. Start the tool gateway (MCP) on the host.

Per claim:

1. Create: `openshell sandbox create --name <claim-id, at most 19 characters> --from claims-agent:1 --policy <policy> --provider claims-nim --no-auto-providers --env MCP_URL=... --env PYTHONPATH=/app --detach -- sleep infinity`. Then call `sandbox exec -- true` until it works.
2. Each turn: the `exec` command of question A. Read the last stdout line as JSON.
3. Denials for the pipeline panel: `openshell logs <name> --source sandbox`.
4. When the claim waits for a person: `openshell sandbox stop <name>`. State is kept (the SQLite file was still there after a restart, and the next turn worked).
5. When the claim closes: `openshell sandbox delete <name>`.

Measured times, image already on the Docker host: create to first working `exec` 0.5 s. Stop then start 0.6 s. An idle sandbox uses 26 MiB (2 containers). An image pull on a new host is the slow part: `python:3.12-slim` took 4.4 minutes on the slow wifi tonight.

Answer: one sandbox per claim is reasonable for the demo. It costs 0.5 s and 26 MiB. Each claim gets its own file system and its own state, so a bad document cannot reach another claim. A long-lived sandbox would save nothing worth the shared state. Not tested: many sandboxes at the same time.

## Question E: OpenShell 0.1.8 (release notes read, not installed)

I read the notes of 0.1.6, 0.1.7 and 0.1.8. I did not run 0.1.8, because NemoForge uses 0.1.5. What to check or change:

| Note | Effect on this setup |
| --- | --- |
| 0.1.6: the gateway refuses to start with no authentication (OIDC, mTLS, or gateway JWT). | The 0.1.5 gateway here uses mTLS, so it may be fine. Check at upgrade. A local gateway can set `allow_unauthenticated_users = true` under `[openshell.gateway.auth]`. |
| 0.1.6: new sandboxes open the control connection from the sandbox to the supervisor. Upgrade supervisor and gateway together. | Restart or recreate sandboxes after the upgrade. |
| 0.1.6: images no longer need `iproute2` or `nftables`. Images with no `USER` run as uid 1000. | None. Our Dockerfile may drop the packages. Keep `USER app` and `run_as_user` in the policy. |
| 0.1.8: static credentials resolve only in the `Authorization` header. Other places need an explicit placement. | None. Our profile uses `auth_style: bearer`, `header_name: authorization`. Import the profile again after the upgrade, because editing the YAML does not update the gateway catalog. |
| 0.1.8: REST endpoints return 403 on HTTP upgrade requests. | None. The model call is a `POST`. |
| 0.1.8: a sandbox fails to start when a filesystem policy path goes through a symlink inside the writable workspace. | Our paths `/usr`, `/lib`, `/etc`, `/app` are outside `/sandbox`. On Debian 13, `/lib` is a link to `/usr/lib`. Test it. |
| 0.1.7: OCSF 1.8.0 only. 0.1.8: attribution fields moved (`device.container.uid`, `device.container.name`). | The pipeline panel must parse the new log format. The shorthand lines in this note may change. Re-run the log checks. |
| 0.1.8: more refusals count as Denied. | Counts of denials can go up. |
| 0.1.6: Docker Engine below 28.0 gives a warning. | Colima has Docker 29.5.2. |
| 0.1.8: the Python SDK is `pip install openshell` (from `ai-openshell/sdk-python`). | The backend can call the SDK instead of the CLI. Not tested. |
| `sandbox create`, `exec`, `stop`, `logs`, `provider create`, `profile import`, `--env`, `--from`, `--policy`, `--provider` | None of the 3 release notes removes or renames them. Not run on 0.1.8. |

## Recommendation for tickets #14 to #16

1. Keep OpenShell 0.1.5 for the base. Move to 0.1.8 in a separate ticket after the demo works, using the table above.
2. #14 (tool gateway as an MCP server): write it in Streamable HTTP. Test a stateful server and the `DELETE` denial. Add authentication (a second provider with `credential_binding`). Allow each tool by name in the policy, and list the same names in `AGENT_TOOLS`. The gateway, not the agent, writes the event of each tool call (non-negotiable 5).
3. #15 (policy and denials in the pipeline panel): start from `policy.yaml` and `profile-nim.yaml`. Parse the log lines of question C. Remove `curl` from the binaries. Keep the `GET /v1/models` check.
4. #16 (deploy on Brev): build the image on the VM, because an image pull is slow on a small network. Run `run-spike.sh` on the VM. Check Landlock there.
5. Agent ticket (backlog item 20): start from `infra/spike/agent/`. Replace the scripted agent in `nim` mode. Print one JSON line per event so the pipeline panel can stream them.
6. Update `docs/ARCHITECTURE.md` sections 1 and 7: they name Hermes. Use D36.

## Not verified

- Concurrent sandboxes, the 429 rate limit of the free key (O15), Guardrails in front of the model.
- The exact MCP revision number negotiated.
- OpenShell 0.1.8, Linux on Brev (kernel with Landlock), Hermes.
- A model that does not call the tool: the agent then answers without data. The backend must not trust the answer text for policy numbers. Read them from the gateway events.
