#!/usr/bin/env bash
# Always-on paper trader: scans every league both venues list, paper-trades any
# fee-positive hedge, settles finished games, and rewrites data/status.json
# after every scan. Restarts itself if the process dies.
#
#   scripts/paper_trader.sh            # foreground (Ctrl-C to stop)
#   nohup scripts/paper_trader.sh >/dev/null 2>&1 &     # background
#   tail -f data/logs/paper_trader.log ; cat data/status.txt ; arb report
set -u
cd "$(dirname "$0")/.."
mkdir -p data/logs
INTERVAL="${INTERVAL:-60}"
LEAGUES="${LEAGUES:-everything}"
while true; do
  echo "$(date -u +%FT%TZ) starting arb run (leagues=$LEAGUES interval=${INTERVAL}s)" >> data/logs/paper_trader.log
  .venv/bin/arb run --leagues "$LEAGUES" --interval "$INTERVAL" --settle-every 5 --status-file data/status.json \
      >> data/logs/paper_trader.log 2>&1
  echo "$(date -u +%FT%TZ) arb run exited ($?), restarting in 30s" >> data/logs/paper_trader.log
  sleep 30
done
