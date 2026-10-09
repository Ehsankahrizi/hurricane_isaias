#!/bin/bash
# Copy new Isaias frames from S3 into Box Drive (run hourly by IsaiasBoxSync.app via launchd).
# Log: ~/Library/Logs/isaias-box-sync.log
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
BUCKET=bil6-isaias-camera-capture-858933856877
DEST="$HOME/Library/CloudStorage/Box-Box/Coastal Hydrology Lab/Ehsan's project/CamerData/Hurricane Isaias/Captures"
echo "=== $(date -u +%FT%TZ)"
mkdir -p "$DEST"
aws s3 sync "s3://$BUCKET/captures/" "$DEST" --only-show-errors --no-progress \
  && echo "synced: $(find "$DEST" -name '*.jpg' -o -name '*.png' | wc -l | tr -d ' ') images in $(find "$DEST" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') camera folders"
# Refresh the interactive map (latest frames) next to the Captures folder.
"$(dirname "$0")/../.venv/bin/python" "$(dirname "$0")/make_map.py" "$(dirname "$DEST")"
