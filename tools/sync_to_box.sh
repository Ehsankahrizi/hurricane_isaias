#!/bin/bash
# Copy new Isaias frames from S3 into Box Drive (run hourly by IsaiasBoxSync.app via launchd).
# S3 → local mirror (outside Box) → Box laid out by day / hour (CDT) / location; then the map.
# Log: ~/Library/Logs/isaias-box-sync.log
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
BUCKET=bil6-isaias-camera-capture-858933856877
MIRROR="$HOME/Library/Application Support/IsaiasBoxSync/mirror"
DEST="$HOME/Library/CloudStorage/Box-Box/Coastal Hydrology Lab/Ehsan's project/CamerData/Hurricane Isaias/Captures"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/../.venv/bin/python"
echo "=== $(date -u +%FT%TZ)"
mkdir -p "$MIRROR"
# One run at a time (a run can outlast the hour if the Mac slept or the network is slow).
LOCK="$MIRROR/../sync.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120)" ]; then rm -rf "$LOCK"; mkdir "$LOCK"; else echo "previous run still going; skipped"; exit 0; fi
fi
trap 'rm -rf "$LOCK"' EXIT
# A failed download (e.g. a dropped connection) is retried next hour; publish what arrived.
aws s3 sync "s3://$BUCKET/captures/" "$MIRROR/captures" --only-show-errors --no-progress --cli-read-timeout 60 \
  || echo "some downloads failed; they are retried next run"
aws s3 cp "s3://$BUCKET/state/manifest.json" "$MIRROR/state/manifest.json.new" --only-show-errors --cli-read-timeout 60 \
  && mv "$MIRROR/state/manifest.json.new" "$MIRROR/state/manifest.json"
[ -f "$MIRROR/state/manifest.json" ] || { echo "no manifest yet"; exit 1; }
"$PY" "$HERE/publish_to_box.py" "$MIRROR" "$DEST" && "$PY" "$HERE/make_map.py" "$(dirname "$DEST")" "$MIRROR"
