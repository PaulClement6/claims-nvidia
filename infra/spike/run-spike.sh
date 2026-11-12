#!/usr/bin/env bash
# Spike of issue #13: a LangGraph agent in an OpenShell 0.1.5 sandbox.
#   A. headless turns from the host (sandbox exec, SQLite checkpointer, one thread per claim)
#   B. one MCP tool on a server outside the sandbox, plain prompt
#   C. confinement: other host, other tool, no key in the sandbox, no host folder
# Needs: OpenShell 0.1.5 gateway (Colima), Docker, uv, NVIDIA_KEY in $ENV_FILE (default: the repo .env).
# Everything created here is named claims-spike*. Removed at the end (use --keep to keep it).
# Run it from a clean state: it refuses to start if the sandbox already exists.
# shellcheck disable=SC2016  # single-quoted scripts expand inside the sandbox
set -u  # no pipefail: grep -q closes pipes early, and each check reads its own exit code

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly here
readonly sandbox="claims-spike-1" provider="claims-spike-nim" profile_id="claims-spike-nim"
readonly image="claims-spike-agent:1" port="${MCP_PORT:-18091}"
readonly mcp_url="http://host.openshell.internal:${port}/mcp"
readonly env_file="${ENV_FILE:-$here/../../.env}"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/default/docker.sock}"
work="$(mktemp -d)"
readonly work
keep=false
[[ "${1:-}" == "--keep" ]] && keep=true
fails=0
mcp_pid=""

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
ok_if() { # ok_if "label" <exit code>
  if [[ "$2" == 0 ]]; then pass "$1"; else fail "$1"; fi
}
now() { python3 -c 'import time; print(time.time())'; }
since() { python3 -c "import time; print(round(time.time() - $1, 1))"; }
jget() { python3 -c 'import json,sys; d=json.loads(sys.stdin.read().strip().splitlines()[-1]); print(eval(sys.argv[1], {"d": d}))' "$1"; }

# The model sometimes writes IDs with a non-breaking hyphen (U+2011). Checks compare ASCII text.
ascii_hyphens() { sed -e 's/\\u2011/-/g' -e 's/\\u2010/-/g' -e 's/\\u2013/-/g'; }

cleanup() {
  if [[ "$keep" == false ]]; then
    openshell sandbox delete "$sandbox" >/dev/null 2>&1
    openshell provider delete "$provider" >/dev/null 2>&1
    openshell profile delete "$profile_id" >/dev/null 2>&1
    [[ -n "$mcp_pid" ]] && kill "$mcp_pid" 2>/dev/null
  fi
  rm -rf "$work"
  return 0
}
trap cleanup EXIT

sx() { openshell sandbox exec --name "$sandbox" --no-tty --no-login-shell -- "$@"; }
# One JSON-RPC POST from the sandbox with curl. Prints the body, then "HTTP <code>".
rpc() {
  sx /bin/sh -c 'curl -s -w "\nHTTP %{http_code}\n" -X POST "$MCP_URL" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-11-25" -d "$1"' sh "$1"
}
turn() { # turn <thread> <message>: one JSON line. The message goes through stdin (no quoting problem).
  printf '%s' "$2" | sx python -m agent --thread "$1"
}
sandbox_log() { openshell logs "$sandbox" --since 30m --source sandbox -n 2000 2>&1; }

echo "== Prerequisites"
[[ "$(openshell --version | awk '{print $2}')" == "0.1.5" ]] || { echo "OpenShell 0.1.5 required"; exit 2; }
openshell status 2>&1 | grep -q Connected || { echo "OpenShell gateway not connected"; exit 2; }
grep -q '^NVIDIA_KEY=.' "$env_file" || { echo "NVIDIA_KEY missing in $env_file"; exit 2; }
openshell sandbox get "$sandbox" >/dev/null 2>&1 && { echo "$sandbox already exists: delete it first"; exit 2; }
openshell provider get "$provider" >/dev/null 2>&1 && { echo "$provider already exists: delete it first"; exit 2; }

echo "== Image (pip packages are baked in at build time: the sandbox has no route to PyPI)"
docker build -q -t "$image" "$here" >/dev/null && pass "image $image built"

echo "== MCP server on the host (port $port)"
MCP_PORT="$port" uv run "$here/mcp_server.py" >"$work/mcp.log" 2>&1 &
mcp_pid=$!
for _ in $(seq 1 90); do
  curl -s -o /dev/null "http://127.0.0.1:$port/mcp" && break
  sleep 0.5
done
curl -s -o /dev/null "http://127.0.0.1:$port/mcp"; ok_if "MCP server answers on 127.0.0.1:$port" $?

echo "== Provider (the key goes through this process environment only)"
openshell profile delete "$profile_id" >/dev/null 2>&1
openshell profile import -f "$here/profile-nim.yaml" >/dev/null || { echo "profile import failed"; exit 2; }
# One variable of this process. Never printed, never a command argument, never a file.
key="$(grep '^NVIDIA_KEY=' "$env_file" | head -1 | cut -d= -f2- | tr -d "\"'")"
NVIDIA_API_KEY="$key" openshell provider create --name "$provider" --type "$profile_id" \
  --credential NVIDIA_API_KEY >/dev/null || { echo "provider create failed"; exit 2; }

echo "== Sandbox"
sed "s/MCP_PORT/$port/" "$here/policy.yaml" >"$work/policy.yaml"
t0="$(now)"
openshell sandbox create --name "$sandbox" --from "$image" --policy "$work/policy.yaml" \
  --provider "$provider" --no-auto-providers --env "MCP_URL=$mcp_url" --env PYTHONPATH=/app \
  --label purpose=issue-13-spike --detach -- sleep infinity >/dev/null || { echo "sandbox create failed"; exit 2; }
until sx true >/dev/null 2>&1; do sleep 0.5; done
echo "sandbox start (create command to first exec): $(since "$t0") s"

echo "== A. Headless turns from the host (one thread = one claim)"
t1="$(now)"; out1="$(turn CLAIM-1 'Which policies does customer C-1001 have?' 2>&1 | ascii_hyphens)"; wall1="$(since "$t1")"
echo "turn 1: $out1"
echo "turn 1 wall time (host, including exec and Python start): $wall1 s"
ok_if "A1 turn 1 returned a JSON answer" "$(jget 'len(d["answer"]) > 0' <<<"$out1" 2>/dev/null | grep -q True; echo $?)"
t2="$(now)"; out2="$(turn CLAIM-1 'Which of the two has the higher limit? Reply with the policy number only.' 2>&1 | ascii_hyphens)"; wall2="$(since "$t2")"
echo "turn 2: $out2"
echo "turn 2 wall time: $wall2 s"
grep -q 'POL-7731-AUTO' <<<"$out2"; ok_if "A2 turn 2 (same thread) remembers the customer and the policies" $?
out3="$(turn CLAIM-2 'Which of the two has the higher limit? Reply with the policy number only.' 2>&1 | ascii_hyphens)"
echo "other thread: $out3"
! grep -q 'POL-7731-AUTO' <<<"$out3"; ok_if "A3 another thread has no memory of claim 1" $?
sx ls -l /sandbox/state/checkpoints.sqlite; ok_if "A4 the conversation state is a file in the scratch folder" $?

echo "== B. MCP tool on a server outside the sandbox (plain prompt, the tool is not named)"
jget 'd["tools"][0]["name"]' <<<"$out1" | grep -qx get_customer_policies; ok_if "B1 the agent called get_customer_policies" $?
grep -q 'POL-7731-AUTO' <<<"$out1" && grep -q 'POL-7732-HOME' <<<"$out1"; ok_if "B2 the answer holds both fake policy numbers" $?
grep -q 'CallToolRequest' "$work/mcp.log"; ok_if "B3 the server on the host logged the call" $?
log="$(sandbox_log)"
echo "MCP methods the langchain-mcp-adapters client sent (all allowed):"
grep -o 'ALLOWED POST http://host.openshell.internal[^ ]* .*rule_methods=[a-z/]*' <<<"$log" | sed 's/.*rule_methods=//' | sort | uniq -c
! grep -q 'DENIED' <<<"$log"; ok_if "B4 no denial during the normal turns: the policy holds what the client needs" $?
grep 'HTTP:POST.*tools/call' <<<"$log" | head -1 | cut -c1-330

echo "== C. Confinement"
echo "-- C1 another host"
c1="$(sx curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.com 2>&1)"; echo "curl https://example.com -> $c1"
[[ "$c1" == 000 ]]; ok_if "C1 a call to another host is denied" $?
sx python -c 'import httpx; httpx.get("https://api.github.com", timeout=10)' 2>&1 | tail -1
c1b="$(sx curl -s -m 10 -o /dev/null -w '%{http_code}' https://integrate.api.nvidia.com/v1/models 2>&1)"
echo "curl https://integrate.api.nvidia.com/v1/models (allowed host, path not in the profile) -> $c1b"
[[ "$c1b" == 403 ]]; ok_if "C1b the model host allows POST /v1/chat/completions only" $?
sx python -m pip download --no-deps --timeout 5 --retries 0 -d /tmp/pipx requests >/dev/null 2>&1
[[ $? != 0 ]]; ok_if "C1c pip cannot reach PyPI: packages must be in the image" $?
echo "-- C2 a tool the policy does not allow, on the allowed server"
c2="$(rpc '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_customer_notes","arguments":{"customer_id":"C-1001"}}}' 2>&1)"
echo "$c2" | cut -c1-260
grep -q 'HTTP 403' <<<"$c2" && grep -q policy_denied <<<"$c2" && ! grep -q secret-note <<<"$c2"; ok_if "C2 get_customer_notes is denied (403 policy_denied)" $?
c2b="$(rpc '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"get_customer_policies","arguments":{"customer_id":"C-1001"}}}' 2>&1)"
grep -q 'POL-7731-AUTO' <<<"$c2b"; ok_if "C2b the allowed tool still works with curl" $?
echo "-- C3 the key"
echo "env | grep -i nvidia (in the sandbox):"
sx /bin/sh -c 'env | grep -i nvidia | sed "s/\(openshell:resolve:env:v\)[0-9]*/\1.../"'
! sx /bin/sh -c 'env | grep -i nvidia' | grep -q 'nvapi-'; ok_if "C3a the environment holds no nvapi- value" $?
find_secret='import os, sys
s = sys.stdin.read().encode()
assert s, "no value on stdin"
if s in b"\0".join(f"{k}={v}".encode() for k, v in os.environ.items()) or s in open("/proc/self/environ", "rb").read():
    sys.exit("FOUND in env")
for root in sys.argv[1:]:
    for d, _, names in os.walk(root):
        for n in names:
            try:
                if s in open(os.path.join(d, n), "rb").read():
                    sys.exit("FOUND " + os.path.join(d, n))
            except OSError:
                pass
print("absent from env, /proc/self/environ and " + " ".join(sys.argv[1:]))'
printf %s "$key" | sx python -c "$find_secret" /sandbox /tmp /etc /app /home /run; ok_if "C3b the real key value is not in the sandbox" $?
printf %s "$key" | docker run -i --rm --entrypoint python "$image" -c "$find_secret" /app /etc /home /usr/local/lib/python3.12/site-packages >/dev/null; ok_if "C3c the real key value is not in the image" $?
unset key
echo "-- C4 host folders"
sx /bin/sh -c 'ls -d /Users /host_mnt /Volumes /home/* 2>&1 | head -5; echo "mounts of a host or Colima folder:"; grep -E "virtiofs|9p|fuse|/Users|host_mnt" /proc/mounts || echo "(none)"'
! sx /bin/sh -c 'grep -E "virtiofs|9p|/Users|host_mnt" /proc/mounts' | grep -q .; ok_if "C4a no host folder is mounted in the sandbox" $?
! sx test -e "/Users"; ok_if "C4b /Users does not exist in the sandbox" $?
c4="$(sx /bin/sh -c 'touch /sandbox/scratch-ok && echo scratch-ok; touch /app/x 2>/dev/null; echo "write-app-exit: $?"')"
grep -q scratch-ok <<<"$c4" && grep -q 'write-app-exit: [1-9]' <<<"$c4"; ok_if "C4c the scratch folder is writable and /app is read-only" $?
echo "-- Denial log lines (openshell logs $sandbox)"
sandbox_log | grep DENIED | cut -c1-380

echo
if [[ "$fails" == 0 ]]; then echo "ALL CHECKS PASSED"; else echo "$fails CHECK(S) FAILED"; fi
exit "$fails"
