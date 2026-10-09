"""Hourly camera capture for Hurricane Isaias (AL092026), Fri Oct 9 – Sun Oct 11 2026 (CDT).

Cameras: traffic (Road511), USGS HIVIS and Windy webcams that are inside the NHC forecast
cone (advisory 10, data/cone.geojson) and within COASTAL_KM of the coast, using the 1,431
HTF stations (data/htf_stations.json) as the coastline.

Every hour on the hour, one current frame is saved from each camera. A frame identical to
the camera's previous one (frozen camera) is not saved again.

S3 layout (bucket = S3_BUCKET):
    captures/<source>_<camera id>/<UTC stamp>.jpg     e.g. traffic_FL-cam-123/2026-10-09T05-00Z.jpg
    captures/cameras.csv                              one row per camera (name, location, distance to coast, frames)
    state/manifest.json                               cameras and every saved frame (time, image time, sha1)
    config/windy_api_key                              private; read at start-up

Usage:
    python isaias_capture.py run        # always-on loop (the ECS task)
    python isaias_capture.py once       # one capture cycle now, then exit
    python isaias_capture.py cameras    # list the cameras that would be captured
"""

import concurrent.futures as cf
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import boto3
import requests
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
S3_BUCKET = os.environ.get("S3_BUCKET", "bil6-isaias-camera-capture-858933856877")
ECS_CLUSTER = os.environ.get("ECS_CLUSTER", "")          # set in the ECS task: stop the service after END
ECS_SERVICE = os.environ.get("ECS_SERVICE", "")
LOCAL_TZ = ZoneInfo("America/Chicago")                   # landfall area (CDT)
START = datetime(2026, 10, 9, 0, 0, tzinfo=LOCAL_TZ)     # Friday 00:00 CDT
END = datetime(2026, 10, 12, 0, 0, tzinfo=LOCAL_TZ)      # Sunday 24:00 CDT
COASTAL_KM = 50.0
MAX_WINDY_PER_QUERY = 50
REDISCOVER_EVERY = timedelta(hours=6)
STALE_AFTER_MIN = 60
USER_AGENT = "Isaias-Camera-Capture/1.0 (research; Coastal Hydrology Lab, University of Alabama)"

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_last_call = {}
HOST_INTERVAL = {"map.road511.com": 1.1}                 # Road511 allows 60 requests/min
WINDY_KEY = os.environ.get("API_WWW_WINDY_COM", "").strip()
_s3 = boto3.client("s3")


# ── Small helpers ───────────────────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


def parse_time(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_")[:80]


def http_get(url, *, params=None, headers=None, timeout=30, retries=3):
    host = urlparse(url).netloc
    for attempt in range(retries):
        wait = HOST_INTERVAL.get(host, 0) - (time.time() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = _session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            print(f"    ! {host}: {e.__class__.__name__}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        return r
    return None


def http_json(url, **kw):
    r = http_get(url, **kw)
    try:
        return r.json() if r is not None and r.ok else None
    except ValueError:
        return None


def dist_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def image_kind(data):
    """'jpg' / 'png' for a real image of a useful size, else None (error pages, placeholders)."""
    if not data or len(data) < 3000:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


# ── Area: cone ∩ coast ──────────────────────────────────────────────────────

CONE = json.loads((HERE / "data" / "cone.geojson").read_text())["geometry"]["coordinates"][0]
HTF = json.loads((HERE / "data" / "htf_stations.json").read_text())


def in_cone(lat, lon):
    """Ray casting on the cone's outer ring (lon, lat)."""
    inside, j = False, len(CONE) - 1
    for i in range(len(CONE)):
        xi, yi = CONE[i]
        xj, yj = CONE[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_cone_lon = [p[0] for p in CONE]
_cone_lat = [p[1] for p in CONE]
# Coast = HTF stations within reach of the cone (a degree of margin covers COASTAL_KM).
COAST = [h for h in HTF if min(_cone_lat) - 1 <= h["lat"] <= max(_cone_lat) + 1
         and min(_cone_lon) - 1 <= h["lon"] <= max(_cone_lon) + 1]


def coast_km(lat, lon):
    return min(dist_km(lat, lon, h["lat"], h["lon"]) for h in COAST) if COAST else 1e9


def in_area(lat, lon):
    return in_cone(lat, lon) and coast_km(lat, lon) <= COASTAL_KM


# ── Camera discovery ────────────────────────────────────────────────────────

def _bbox():
    """Search box for Road511: cone longitudes × latitudes of coast stations near the cone."""
    near = [h for h in COAST if any(in_cone(h["lat"] + dy, h["lon"] + dx) for dy in (-0.45, 0, 0.45) for dx in (-0.45, 0, 0.45))]
    lat0, lat1 = min(h["lat"] for h in near) - 0.5, max(h["lat"] for h in near) + 0.5
    return lat0, lat1, min(_cone_lon), max(_cone_lon)


def traffic_cameras():
    lat0, lat1, lon0, lon1 = _bbox()
    found, y = {}, lat0
    while y < lat1:
        x = lon0
        while x < lon1:
            cursor = None
            for _ in range(30):
                params = {"type": "cameras", "bbox": f"{x:.4f},{y:.4f},{min(x + 1, lon1):.4f},{min(y + 1, lat1):.4f}", "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                j = http_json("https://map.road511.com/api/v1/map/features", params=params)
                if not j:
                    break
                for c in j.get("data") or []:
                    found[c["id"]] = c
                cursor = j.get("next_cursor") if j.get("has_more") else None
                if not cursor:
                    break
            x += 1
        y += 1
    out = []
    for c in found.values():
        lat, lon = c.get("latitude"), c.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or not in_area(lat, lon):
            continue
        p = c.get("properties") or {}
        image = p.get("image_url") or (p["image_urls"][0] if isinstance(p.get("image_urls"), list) and p["image_urls"] else None)
        status = str(p.get("camera_status") or p.get("status") or "").lower()
        if not image or c.get("is_active") is False or re.search(r"offline|out_of_service|disabled|inactive", status):
            continue
        out.append({"source": "traffic", "id": str(c["id"]), "name": c.get("name") or p.get("description") or "Traffic camera",
                    "lat": lat, "lon": lon, "provider": f"511 · {c.get('source')}", "image_url": image,
                    "page_url": p.get("url") or image})
    return out


def usgs_cameras():
    j = http_json("https://api.waterdata.usgs.gov/nims/v0/cameras",
                  params={"returnFields": "camId,camName,lat,lng,newestImageDT,hideCam,overlayDir,smallDir"})
    out = []
    for c in j or []:
        if c.get("hideCam") or not c.get("lat") or not c.get("lng") or not c.get("newestImageDT"):
            continue
        lat, lon = float(c["lat"]), float(c["lng"])
        if not in_area(lat, lon) or (utcnow() - parse_time(c["newestImageDT"])).days > 7:
            continue
        out.append({"source": "usgs", "id": c["camId"], "name": c.get("camName") or c["camId"], "lat": lat, "lon": lon,
                    "provider": "USGS HIVIS", "image_dir": c.get("overlayDir") or c.get("smallDir"),
                    "page_url": f"https://apps.usgs.gov/hivis/camera/{c['camId']}"})
    return out


WINDY_API = "https://api.windy.com/webcams/api/v3/webcams"


def windy_cameras():
    if not WINDY_KEY:
        return []
    centers = [h for h in COAST if in_cone(h["lat"], h["lon"])] or COAST
    found = {}
    for h in centers:
        j = http_json(WINDY_API, params={"nearby": f"{h['lat']:.4f},{h['lon']:.4f},{int(COASTAL_KM)}",
                                         "limit": MAX_WINDY_PER_QUERY, "include": "location,urls", "lang": "en"},
                      headers={"x-windy-api-key": WINDY_KEY, "Accept": "application/json"})
        for w in (j or {}).get("webcams", []):
            loc = w.get("location") or {}
            if loc.get("latitude") is None or w.get("status", "active") != "active":
                continue
            lat, lon = float(loc["latitude"]), float(loc["longitude"])
            if in_area(lat, lon):
                wid = str(w.get("webcamId") or w.get("id"))
                found[wid] = {"source": "windy", "id": wid, "name": w.get("title") or "Windy webcam", "lat": lat, "lon": lon,
                              "provider": "Windy.com",
                              "page_url": (w.get("urls") or {}).get("detail") or f"https://www.windy.com/webcams/{wid}"}
    return list(found.values())


def discover():
    cams = traffic_cameras() + usgs_cameras() + windy_cameras()
    for c in cams:
        c["coast_km"] = round(coast_km(c["lat"], c["lon"]), 2)
        c["folder"] = f"{c['source']}_{safe(c['id'])}"
    cams.sort(key=lambda c: (c["source"], c["coast_km"]))
    counts = {s: sum(c["source"] == s for c in cams) for s in ("traffic", "usgs", "windy")}
    print(f"Cameras in cone and within {COASTAL_KM:.0f} km of the coast: {len(cams)} {counts}", flush=True)
    return cams


# ── One frame per camera ────────────────────────────────────────────────────

def http_date(value):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).astimezone(timezone.utc) if value else None
    except (TypeError, ValueError):
        return None


def grab(cam, now):
    """(bytes, image_time or None, method) for the camera's current image, or (None, None, None)."""
    if cam["source"] == "traffic":
        sep = "&" if "?" in cam["image_url"] else "?"
        r = http_get(f"{cam['image_url']}{sep}_t={int(now.timestamp())}", timeout=25, retries=2)
        if r is not None and r.ok:
            return r.content, http_date(r.headers.get("Last-Modified")), "snapshot"
    elif cam["source"] == "usgs":
        names = http_json("https://api.waterdata.usgs.gov/nims/v0/listFiles",
                          params={"camId": cam["id"], "after": iso(now - timedelta(hours=1)), "before": iso(now),
                                  "recent": "false", "limit": 500})
        best = None
        for name in names or []:
            m = re.search(r"___(\d{4}-\d\d-\d\d)T(\d\d)-(\d\d)-(\d\d)Z", str(name))
            if m and cam.get("image_dir"):
                t = parse_time(f"{m[1]}T{m[2]}:{m[3]}:{m[4]}Z")
                if best is None or t > best[0]:
                    best = (t, cam["image_dir"] + name)
        if best:
            r = http_get(best[1], timeout=40, retries=2)
            if r is not None and r.ok:
                return r.content, best[0], "usgs_latest"
    elif cam["source"] == "windy" and WINDY_KEY:
        j = http_json(f"{WINDY_API}/{cam['id']}", params={"include": "images"},
                      headers={"x-windy-api-key": WINDY_KEY, "Accept": "application/json"})
        img = ((j or {}).get("images") or {}).get("current") or {}
        url = img.get("preview") or img.get("thumbnail")
        if url:
            r = http_get(url, timeout=25, retries=2)
            if r is not None and r.ok:
                updated = (j or {}).get("lastUpdatedOn")
                return r.content, parse_time(updated) if updated else None, "windy_current"
    return None, None, None


# ── S3 state ────────────────────────────────────────────────────────────────

def s3_json(key, default):
    try:
        return json.loads(_s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return default
        raise


def put(key, body, content_type):
    _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType=content_type)


def load_windy_key():
    global WINDY_KEY
    if WINDY_KEY:
        return
    try:
        WINDY_KEY = _s3.get_object(Bucket=S3_BUCKET, Key="config/windy_api_key")["Body"].read().decode().strip()
        print("Windy key loaded from S3")
    except ClientError:
        print("No Windy key in S3 (config/windy_api_key); Windy webcams are skipped")


def write_index(manifest):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["folder", "source", "camera_id", "name", "provider", "lat", "lon", "distance_to_coast_km",
                "frames", "first_frame_utc", "last_frame_utc", "page_url"])
    caps = manifest["captures"]
    for c in manifest["cameras"]:
        mine = sorted(x["time"] for x in caps if x["folder"] == c["folder"])
        w.writerow([c["folder"], c["source"], c["id"], c["name"], c.get("provider", ""), c["lat"], c["lon"],
                    c["coast_km"], len(mine), mine[0] if mine else "", mine[-1] if mine else "", c.get("page_url", "")])
    put("captures/cameras.csv", buf.getvalue().encode(), "text/csv")
    put("state/manifest.json", json.dumps(manifest, indent=1).encode(), "application/json")


# ── Cycle and loop ──────────────────────────────────────────────────────────

def capture_cycle(slot):
    """Save one frame per camera for the hour `slot` (UTC, on the hour)."""
    t0 = time.time()
    manifest = s3_json("state/manifest.json", {"cameras": [], "captures": [], "discovered": None})
    if not manifest["cameras"] or not manifest["discovered"] or utcnow() - parse_time(manifest["discovered"]) > REDISCOVER_EVERY:
        known = {c["folder"]: c for c in manifest["cameras"]}
        for c in discover():
            known[c["folder"]] = c                           # keep cameras found earlier, too
        manifest["cameras"] = sorted(known.values(), key=lambda c: (c["source"], c["coast_km"]))
        manifest["discovered"] = iso(utcnow())
    done = {(x["folder"], x["slot"]) for x in manifest["captures"]}
    last_sha = {}
    for x in manifest["captures"]:
        last_sha[x["folder"]] = x["sha1"]
    todo = [c for c in manifest["cameras"] if (c["folder"], iso(slot)) not in done]
    now = utcnow()

    def one(cam):
        try:
            return cam, grab(cam, now)
        except Exception as e:                               # one camera must never stop the cycle
            print(f"    ! {cam['folder']}: {e}")
            return cam, (None, None, None)

    saved = frozen = failed = 0
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for cam, (data, image_time, method) in pool.map(one, todo):
            kind = image_kind(data)
            if not kind:
                failed += 1
                continue
            sha = hashlib.sha1(data).hexdigest()
            if last_sha.get(cam["folder"]) == sha:
                frozen += 1
                continue
            stamp = now.strftime("%Y-%m-%dT%H-%MZ")
            key = f"captures/{cam['folder']}/{stamp}.{kind}"
            put(key, data, "image/jpeg" if kind == "jpg" else "image/png")
            if image_time and image_time > now:
                image_time = now
            age = round((now - image_time).total_seconds() / 60, 1) if image_time else None
            manifest["captures"].append({
                "folder": cam["folder"], "slot": iso(slot), "time": iso(now), "time_local": local(now),
                "image_time": iso(image_time) if image_time else None, "age_min": age,
                "stale": age is not None and age > STALE_AFTER_MIN, "method": method, "sha1": sha,
                "file": key, "bytes": len(data)})
            last_sha[cam["folder"]] = sha
            saved += 1
    write_index(manifest)
    print(f"── {local(slot)} ({iso(slot)}): saved {saved}, unchanged {frozen}, failed {failed} "
          f"of {len(todo)} cameras in {time.time() - t0:.0f} s", flush=True)


def stop_service():
    if ECS_CLUSTER and ECS_SERVICE:
        try:
            boto3.client("ecs").update_service(cluster=ECS_CLUSTER, service=ECS_SERVICE, desiredCount=0)
            print("Capture period over: ECS service set to 0 tasks", flush=True)
        except Exception as e:
            print(f"Could not stop the ECS service ({e}); set its desired count to 0 by hand", flush=True)


def run():
    load_windy_key()
    print(f"Isaias capture: bucket {S3_BUCKET}, {local(START)} → {local(END)}, every hour", flush=True)
    while True:
        now = utcnow()
        if now >= END:
            stop_service()
            while True:                                      # wait to be stopped; never restart captures
                time.sleep(3600)
        slot = max(now.replace(minute=0, second=0, microsecond=0), START)
        if now >= START:
            try:
                capture_cycle(slot)
            except Exception:
                traceback.print_exc()
                print("── cycle FAILED", flush=True)
        nxt = max(slot + timedelta(hours=1), START)
        time.sleep(max(5, (nxt - utcnow()).total_seconds()))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "once":
        load_windy_key()
        capture_cycle(utcnow().replace(minute=0, second=0, microsecond=0))
    elif cmd == "cameras":
        load_windy_key()
        for c in discover():
            print(f"  {c['folder']:40} {c['coast_km']:6.1f} km  {c['name']}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
