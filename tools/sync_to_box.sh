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
aws s3 sync "s3://$BUCKET/captures/" "$MIRROR/captures" --only-show-errors --no-progress \
  && aws s3 cp "s3://$BUCKET/state/manifest.json" "$MIRROR/state/manifest.json" --only-show-errors \
  && "$PY" "$HERE/publish_to_box.py" "$MIRROR" "$DEST" \
  && "$PY" "$HERE/make_map.py" "$(dirname "$DEST")" "$MIRROR"
