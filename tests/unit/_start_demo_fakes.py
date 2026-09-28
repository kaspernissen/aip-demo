"""Shared fake `curl` for tests that run the real `scripts/start-demo.sh`.

An unconditionally successful fake curl (`"#!/usr/bin/env bash\\nexit 0\\n"`)
cannot distinguish "the surface answers" from "it does not" -- which is
exactly the dimension `verify_owned_forward` and `verify_existing_surface`
exist to check: a bound port proves nothing, only an actual HTTP response
does. Mutating the identical line of `scripts/start-demo.sh:101` under each
fake shows the difference: it comes back GREEN under an always-succeeds fake
and RED under this one.

This module is the shared, port-aware fake both `test_start_demo_v11.py` and
`test_stage4_config.py` use, taking the set of refusing ports as a parameter
instead of hard-coding which port refuses.

Named with a leading underscore and no `test_` prefix so pytest's own
collection never picks it up as a test module (same convention as
`_count_gated_plugin.py`).
"""
from __future__ import annotations

from collections.abc import Iterable


def port_aware_fake_curl(refusing_ports: Iterable[str] = ()) -> str:
    """Bash source for a fake `curl` that answers for every port EXCEPT the
    ones named in `refusing_ports` -- which it fails, exactly as a dead
    `kubectl port-forward` or an unresponsive foreign holder does. Ports are
    matched as strings (e.g. `"18080"`, `"8081"`), read out of the
    `http://localhost:<port>/...` URL `start-demo.sh` invokes curl with.

    An empty `refusing_ports` (the default) matches an always-succeeds fake
    exactly, for tests that only need curl to get out of the way.
    """
    refusing = " ".join(sorted(refusing_ports))
    return f"""#!/usr/bin/env bash
port=""
for arg in "$@"; do
  case "$arg" in
    http://localhost:*)
      rest="${{arg#http://localhost:}}"
      port="${{rest%%/*}}"
      ;;
  esac
done
for refusing in {refusing}; do
  if [[ "$port" == "$refusing" ]]; then
    exit 1
  fi
done
exit 0
"""
