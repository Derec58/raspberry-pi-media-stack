#!/bin/bash
# Periodically audit the comics library and refresh the Kavita reading lists, so
# issues that finish downloading later slot into their correct reading-order
# position without anyone having to intervene.
#
# Cadence is 60 minutes, not the 20 it used to be. The sync script now skips the
# Kavita scan entirely when no library changed on disk, so a quiet cycle costs
# about two seconds and touches Kavita not at all. Polling harder gained nothing
# and, before the scan was made conditional and sequential, actively caused
# Kavita to defer scans by three hours.
#
# Run it detached and it will keep going across sessions:
#     nohup scripts/comics-autosync.sh >/dev/null 2>&1 &
# Stop it with:
#     pkill -f comics-autosync.sh

set -u
cd "$(dirname "$0")/.." || exit 1

INTERVAL="${AUTOSYNC_INTERVAL:-3600}"
CYCLES="${AUTOSYNC_CYCLES:-0}"       # 0 = run until stopped
LOG=logs/autosync.log

mkdir -p logs

i=0
while :; do
    i=$((i + 1))
    {
        echo "───── run $i  $(date '+%F %H:%M:%S')"
        ./scripts/verify-comics-library.py --quiet 2>&1 \
            | grep -vE '^\s*$' | sed 's/^/  verify: /'
        ./scripts/sync-kavita-reading-lists.py 2>&1 \
            | grep -E 'scanned|skipping scan|WARNING|issues resolved|^Scarlet|resolved [0-9]+' \
            | sed 's/^/  sync: /'
    } >>"$LOG" 2>&1

    [ "$CYCLES" -gt 0 ] && [ "$i" -ge "$CYCLES" ] && break
    sleep "$INTERVAL"
done

echo "autosync finished $(date)" >>"$LOG"
