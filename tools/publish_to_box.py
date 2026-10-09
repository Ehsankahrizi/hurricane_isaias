#!/usr/bin/env python3
"""Lay out the frames in Box by day and hour (CDT), one image per location in each hour.

The S3 bucket keeps one folder per camera (captures/<camera>/<UTC stamp>.jpg). The hourly job
copies it into a local mirror outside Box, and this script copies each frame into Box as

    Captures/<YYYY-MM-DD Day>/<HH-00 CDT>/<camera> - <location name>.jpg

using the capture hour ("slot") from state/manifest.json. Only new or changed files are
copied, and camera folders from the earlier per-camera layout are removed from Box (the
frames are in the mirror and in S3). Standard library only.

Usage:  python3 tools/publish_to_box.py <mirror> "<Box>/Hurricane Isaias/Captures"
"""

import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5), "CDT")


def clean(name):
    """A location name that is safe in a file name."""
    return re.sub(r"\s+", " ", re.sub(r'[/\\:*?"<>|]+', "-", str(name))).strip(" .-")[:70]


def hour_folder(slot):
    t = datetime.fromisoformat(slot.replace("Z", "+00:00")).astimezone(CDT)
    return t.strftime("%Y-%m-%d %a"), t.strftime("%H-00 CDT")


def main():
    mirror, box = Path(sys.argv[1]), Path(sys.argv[2])
    manifest = json.loads((mirror / "state" / "manifest.json").read_text())
    names = {c["folder"]: clean(c["name"]) for c in manifest["cameras"]}
    box.mkdir(parents=True, exist_ok=True)
    copied = 0
    for cap in manifest["captures"]:
        src = mirror / cap["file"]                         # captures/<camera>/<stamp>.jpg
        if not src.exists():
            continue
        day, hour = hour_folder(cap["slot"])
        label = f"{cap['folder']} - {names[cap['folder']]}" if names.get(cap["folder"]) else cap["folder"]
        dst = box / day / hour / f"{label}{src.suffix}"
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    index = mirror / "captures" / "cameras.csv"
    if index.exists():
        shutil.copy2(index, box / "cameras.csv")
    removed = 0
    for d in box.iterdir():                                # earlier layout: one folder per camera
        if d.is_dir() and re.match(r"^(traffic|usgs|windy|webcoos)_", d.name):
            shutil.rmtree(d)
            removed += 1
    days = sorted(d.name for d in box.iterdir() if d.is_dir())
    hours = sum(1 for d in box.iterdir() if d.is_dir() for _ in d.iterdir())
    print(f"box: {copied} new frame(s); {len(days)} day(s), {hours} hour folder(s)"
          + (f"; removed {removed} old camera folder(s)" if removed else ""))


if __name__ == "__main__":
    main()
