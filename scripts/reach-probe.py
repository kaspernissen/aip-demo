# The prober that runs INSIDE a meshed pod, as `python3 -c "$(cat this)"
# <url> <GET|POST> <timeout>`. One file, two callers: scripts/beat-reach.sh and
# scripts/demo (`demo guard`), so the stage path and the gate path ask the
# cluster the same question with the same code.
#
# Prints exactly one line:
#   REACHED <http status>   -- something answered: a status line came back (a
#                              401 from the app is "reached"; so is a 200)
#   REFUSED <http status>   -- Linkerd refused it at the destination: an empty
#                              403 (wrong identity) or 404 (no route matched),
#                              no `l5d-proxy-error` header. The caller reads
#                              the destination's deny counter around the call
#                              to name the policy; this file cannot (it has no
#                              kubectl).
#   DROPPED <how>           -- nothing answered: `timeout`, `reset`, or
#                              `proxy-error` (this pod's own proxy could not
#                              reach the peer, because the node dropped its
#                              packets). From a meshed pod a TCP connect
#                              "succeeds" for every address, so the verdict is
#                              always read from the HTTP exchange, never the
#                              connect. The same rules as
#                              tests/harness/reach.py (pinned together by
#                              tests/unit/test_reach_probe_script.py).
import json, sys, urllib.request, urllib.error, socket
url, method, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
body = None
headers = {}
if method == "POST":
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "beat-reach", "version": "1"}}}).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
req = urllib.request.Request(url, data=body, headers=headers, method=method)


def how_dropped(exc):
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "reset"
    return type(exc).__name__


try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()
        print("REACHED", r.status)
except urllib.error.HTTPError as e:
    data = e.read()
    if e.headers.get("l5d-proxy-error"):
        print("DROPPED proxy-error")
    elif e.code in (403, 404) and not data:
        print("REFUSED", e.code)
    else:
        print("REACHED", e.code)
except urllib.error.URLError as e:
    print("DROPPED", how_dropped(e.reason))
except Exception as e:
    print("DROPPED", how_dropped(e))
