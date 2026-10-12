# The prober that runs INSIDE claims-agent's pod, as `python3 -c "$(cat this)"
# <url> <GET|POST> <timeout>`. One file, two callers: scripts/beat-reach.sh and
# scripts/demo (`demo guard`), so the stage path and the gate path ask the
# cluster the same question with the same code.
#
# Prints exactly one line:
#   REACHED <http status>        -- something answered
#   DROPPED timeout              -- nothing answered: the packet was dropped
#   REFUSED <exception name>     -- something actively refused (an RST, a reset)
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
try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        print("REACHED", r.status)
except urllib.error.HTTPError as e:
    print("REACHED", e.code)
except urllib.error.URLError as e:
    # urllib wraps a connect timeout in URLError; the wrapped reason is what
    # tells a Cilium drop (timeout) from a refusal (ConnectionRefusedError).
    reason = e.reason
    if isinstance(reason, (socket.timeout, TimeoutError)):
        print("DROPPED timeout")
    else:
        print("REFUSED", type(reason).__name__)
except (socket.timeout, TimeoutError):
    print("DROPPED timeout")
except Exception as e:
    print("REFUSED", type(e).__name__)
