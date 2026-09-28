#!/usr/bin/env bash
# Serve the deck and open it next to the speaker-notes follower.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8002}"
# ./start.sh preview-30-act3a.html serves a fragment preview instead of the full deck.
DECK="${1:-index.html}"

python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
sleep 1

echo "Deck:  http://localhost:$PORT/$DECK"
echo "Notes: http://localhost:$PORT/notes.html?deck=$DECK"
echo "Keys:  arrows / space to advance, Home / End, R to reset. Ctrl-C to stop."

if command -v open >/dev/null; then
  open "http://localhost:$PORT/$DECK"
  open "http://localhost:$PORT/notes.html?deck=$DECK"
fi

wait $SERVER
