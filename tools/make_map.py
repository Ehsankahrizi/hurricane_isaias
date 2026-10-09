#!/usr/bin/env python3
"""Write the interactive Hurricane Isaias camera map (one HTML file) into the Box folder.

Shows the NHC cone (advisory 10), forecast track, watches/warnings, the coastal HTF stations
and every captured camera, colored by source. Clicking a camera shows its latest frame;
the arrows step through all its hourly frames. Frames are read from the Captures folder next
to the map, so the map works when opened from Box Drive (or a downloaded copy of the folder).

Run by tools/sync_to_box.sh after every Box copy. Standard library only.

Usage:  python3 tools/make_map.py "<Box>/Hurricane Isaias" <mirror>
"""

import csv
import json
import sys as _sys
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"
MAP_NAME = "Isaias_camera_map.html"
SOURCE_LABEL = {"traffic": "Traffic camera (state DOT)", "usgs": "USGS HIVIS", "windy": "Windy webcam"}


_sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_to_box import clean, hour_folder                    # noqa: E402  (same Box layout)


def cameras(captures, mirror):
    """Cameras from cameras.csv, each with its frames as [path relative to the map, CDT label]."""
    out = []
    index = captures / "cameras.csv"
    rows = list(csv.DictReader(index.open())) if index.exists() else []
    manifest = json.loads((mirror / "state" / "manifest.json").read_text())
    names = {c["folder"]: clean(c["name"]) for c in manifest["cameras"]}
    by_cam = {}
    for cap in sorted(manifest["captures"], key=lambda c: c["slot"]):
        day, hour = hour_folder(cap["slot"])
        label = f"{cap['folder']} - {names[cap['folder']]}" if names.get(cap["folder"]) else cap["folder"]
        rel = f"Captures/{day}/{hour}/{label}{Path(cap['file']).suffix}"
        if (captures.parent / rel).exists():
            by_cam.setdefault(cap["folder"], []).append([rel, f"{day} · {hour}"])
    for r in rows:
        frames = by_cam.get(r["folder"], [])
        out.append({"folder": r["folder"], "source": r["source"], "id": r["camera_id"], "name": r["name"],
                    "provider": r.get("provider", ""), "lat": float(r["lat"]), "lon": float(r["lon"]),
                    "coast_km": float(r["distance_to_coast_km"]), "page": r.get("page_url", ""),
                    "frames": frames})
    order = {"windy": 0, "usgs": 1, "traffic": 2}             # traffic cameras drawn on top
    return sorted(out, key=lambda c: order.get(c["source"], 0))


def coast_stations(cone):
    ring = cone["geometry"]["coordinates"][0]
    lo0, lo1 = min(p[0] for p in ring) - 1, max(p[0] for p in ring) + 1
    la0, la1 = min(p[1] for p in ring) - 1, max(p[1] for p in ring) + 1
    return [[h["lat"], h["lon"], h["htf_id"]] for h in json.loads((DATA / "htf_stations.json").read_text())
            if la0 <= h["lat"] <= la1 and lo0 <= h["lon"] <= lo1]


def main():
    box = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Library/CloudStorage/Box-Box/Coastal Hydrology Lab/Ehsan's project/CamerData/Hurricane Isaias"
    cone = json.loads((DATA / "cone.geojson").read_text())
    data = {
        "cone": cone,
        "track": json.loads((DATA / "track_line.geojson").read_text()),
        "points": json.loads((DATA / "track_points.geojson").read_text()),
        "warnings": json.loads((DATA / "warnings.geojson").read_text()),
        "stations": coast_stations(cone),
        "cameras": cameras(box / "Captures", Path(sys.argv[2]) if len(sys.argv) > 2
                           else Path.home() / "Library/Application Support/IsaiasBoxSync/mirror"),
        "labels": SOURCE_LABEL,
        "updated": (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%a %b %d %H:%M CDT"),
    }
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    out = box / MAP_NAME
    tmp = out.with_suffix(".tmp")
    tmp.write_text(html)
    tmp.replace(out)
    n = sum(len(c["frames"]) for c in data["cameras"])
    print(f"map: {len(data['cameras'])} cameras, {n} frames → {out}")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isaias Camera Map</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  :root { --bg:#fff; --fg:#1d2433; --muted:#5b6475; --line:#d9dee7; --panel:rgba(255,255,255,.96);
          --traffic:#1f6fd1; --usgs:#0f9d8a; --windy:#d9480f; --cone:#c92a2a; --track:#212529; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--fg);
               font:14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  #map { position:absolute; inset:0; }
  .panel { position:absolute; z-index:1000; background:var(--panel); border:1px solid var(--line);
           border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,.12); padding:12px 14px; }
  #info { top:12px; left:56px; max-width:360px; }
  #info h1 { font-size:16px; margin:0 0 4px; }
  #info p { margin:2px 0; color:var(--muted); font-size:12.5px; }
  #legend { bottom:22px; left:12px; font-size:12.5px; }
  #legend label { display:flex; align-items:center; gap:8px; margin:3px 0; cursor:pointer; }
  .dot { width:12px; height:12px; border-radius:50%; border:2px solid #fff; box-shadow:0 0 0 1px rgba(0,0,0,.35); flex:none; }
  .swatch { width:16px; height:10px; flex:none; border-radius:2px; }
  #search { width:100%; margin-top:8px; padding:6px 8px; border:1px solid var(--line); border-radius:6px; font:inherit; }
  .pop { width:330px; }
  .pop h3 { font-size:14px; margin:0 0 2px; }
  .pop .meta { color:var(--muted); font-size:12px; margin-bottom:6px; }
  .pop img { width:100%; border-radius:6px; background:#eef1f5; min-height:120px; display:block; }
  .pop .nav { display:flex; align-items:center; justify-content:space-between; gap:6px; margin-top:6px; }
  .pop button { border:1px solid var(--line); background:#f6f8fb; border-radius:6px; padding:3px 10px; cursor:pointer; font:inherit; }
  .pop button:disabled { opacity:.4; cursor:default; }
  .pop .when { font-size:12.5px; text-align:center; flex:1; }
  .pop .links { margin-top:6px; font-size:12px; display:flex; gap:12px; }
  .none { color:var(--muted); font-style:italic; padding:20px 0; text-align:center; }
  @media (max-width:600px) {
    #info { left:12px; right:12px; top:auto; bottom:12px; max-width:none; }
    #legend { top:64px; bottom:auto; left:auto; right:10px; }
    .pop { width:260px; }
  }
</style>
</head>
<body>
<div id="map"></div>
<div id="info" class="panel">
  <h1>Hurricane Isaias · coastal cameras</h1>
  <p id="summary"></p>
  <p>NHC advisory 10 (10 PM CDT Thu Oct 8). Frames every hour, Fri Oct 9 – Sun Oct 11 (CDT).</p>
  <input id="search" type="search" placeholder="Find a camera (name or ID)…">
</div>
<div id="legend" class="panel"></div>
<script>
const D = /*DATA*/null;
const COLOR = { traffic: "var(--traffic)", usgs: "var(--usgs)", windy: "var(--windy)" };
const HEX = { traffic: "#1f6fd1", usgs: "#0f9d8a", windy: "#d9480f" };
const map = L.map("map", { zoomControl: true }).setView([30.4, -87.2], 8);
// OpenStreetMap's and CARTO's tile servers refuse pages opened as local files (no referrer),
// so the basemaps come from Esri, which allows that.
const esri = (svc, attr) => L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/${svc}/MapServer/tile/{z}/{y}/{x}`,
  { maxZoom: 19, maxNativeZoom: svc.includes("Canvas") ? 16 : 19, attribution: `${attr} · Cone/track: NOAA NHC` });
const base = {
  "Streets": esri("World_Street_Map", "Tiles &copy; Esri, HERE, Garmin, OpenStreetMap contributors"),
  "Satellite": esri("World_Imagery", "Imagery &copy; Esri, Maxar, Earthstar Geographics"),
  "Light gray": esri("Canvas/World_Light_Gray_Base", "Tiles &copy; Esri, HERE, Garmin"),
};
base["Streets"].addTo(map);
L.control.layers(base, null, { position: "topright", collapsed: true }).addTo(map);

const cone = L.geoJSON(D.cone, { style: { color: "#c92a2a", weight: 2, fillColor: "#ff6b6b", fillOpacity: .12 } }).addTo(map);
const WW = { HWR: ["Hurricane warning", "#c92a2a"], HWA: ["Hurricane watch", "#f783ac"],
             TWR: ["Tropical storm warning", "#1971c2"], TWA: ["Tropical storm watch", "#ffd43b"] };
const warnings = L.geoJSON(D.warnings, { style: f => ({ color: (WW[f.properties.TCWW] || ["", "#555"])[1], weight: 6, opacity: .9 }),
  onEachFeature: (f, l) => l.bindTooltip((WW[f.properties.TCWW] || [f.properties.TCWW])[0]) }).addTo(map);
const track = L.geoJSON(D.track, { style: { color: "#212529", weight: 2, dashArray: "6 5" } }).addTo(map);
const points = L.geoJSON(D.points, { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, color: "#212529", weight: 2, fillColor: "#fff", fillOpacity: 1 }),
  onEachFeature: (f, l) => { const p = f.properties;
    l.bindTooltip(`<b>${p.FLDATELBL}</b><br>${p.STORMTYPE} · ${p.MAXWIND} kt (gusts ${p.GUST} kt)${p.MSLP && p.MSLP < 9999 ? " · " + p.MSLP + " mb" : ""}`); } }).addTo(map);
const stations = L.layerGroup(D.stations.map(s => L.circleMarker([s[0], s[1]], { radius: 3, color: "#868e96", weight: 1, fillOpacity: .8 })
  .bindTooltip(`HTF station ${s[2]}`)));

const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const groups = {}, markers = [];
function popup(c) {
  const el = document.createElement("div"); el.className = "pop";
  let i = c.frames.length - 1;
  el.innerHTML = `<h3>${esc(c.name)}</h3>
    <div class="meta">${esc(D.labels[c.source] || c.source)} · ${esc(c.id)} · ${c.coast_km.toFixed(1)} km from the coast</div>
    ${c.frames.length ? `<img alt="Camera frame"><div class="nav"><button data-d="-1">◀</button><span class="when"></span><button data-d="1">▶</button></div>`
                      : `<div class="none">No frames yet</div>`}
    <div class="links"><a class="open" target="_blank">Open image</a>
      ${c.page ? `<a href="${esc(c.page)}" target="_blank" rel="noopener">Camera page</a>` : ""}</div>`;
  const img = el.querySelector("img"), when = el.querySelector(".when"), btns = el.querySelectorAll("button"),
        open_ = el.querySelector(".open");
  if (!img) open_.remove();
  function show() {
    if (!img) return;
    const [file, label] = c.frames[i];
    img.src = file.split("/").map(encodeURIComponent).join("/");
    open_.href = img.src;
    when.textContent = `${label}  (${i + 1}/${c.frames.length})`;
    btns[0].disabled = i === 0; btns[1].disabled = i === c.frames.length - 1;
  }
  btns.forEach(b => b.addEventListener("click", e => { e.stopPropagation(); i = Math.max(0, Math.min(c.frames.length - 1, i + +b.dataset.d)); show(); }));
  show();
  return el;
}
for (const c of D.cameras) {
  const m = L.circleMarker([c.lat, c.lon], { radius: 6, color: "#fff", weight: 1.5, fillColor: HEX[c.source] || "#555", fillOpacity: .95 })
    .bindTooltip(`${esc(c.name)} · ${c.frames.length} frames`)
    .bindPopup(() => popup(c), { maxWidth: 360, minWidth: 260 });
  (groups[c.source] = groups[c.source] || L.layerGroup().addTo(map)).addLayer(m);
  markers.push([c, m]);
}

const counts = {}; D.cameras.forEach(c => counts[c.source] = (counts[c.source] || 0) + 1);
const nFrames = D.cameras.reduce((a, c) => a + c.frames.length, 0);
document.getElementById("summary").textContent = `${D.cameras.length} cameras · ${nFrames.toLocaleString()} frames · updated ${D.updated}`;
const leg = document.getElementById("legend");
function row(html, layer, on = true) {
  const l = document.createElement("label");
  l.innerHTML = `<input type="checkbox" ${on ? "checked" : ""}> ${html}`;
  l.querySelector("input").addEventListener("change", e => e.target.checked ? layer.addTo(map) : map.removeLayer(layer));
  leg.appendChild(l);
}
for (const s of Object.keys(groups)) row(`<span class="dot" style="background:${COLOR[s]}"></span>${esc(D.labels[s] || s)} (${counts[s]})`, groups[s]);
row(`<span class="swatch" style="background:#ffc9c9;border:2px solid #c92a2a"></span>Forecast cone`, cone);
row(`<span class="swatch" style="background:linear-gradient(90deg,#c92a2a 50%,#1971c2 50%)"></span>Watches / warnings`, warnings);
row(`<span class="swatch" style="border-top:2px dashed #212529;height:0"></span>Forecast track`, L.layerGroup([track, points]).addTo(map));
row(`<span class="dot" style="background:#868e96;width:8px;height:8px"></span>HTF stations (coast)`, stations, false);
map.fitBounds(L.featureGroup(markers.map(x => x[1])).getBounds().pad(0.15));

// On narrow screens the panels would cover the popup: hide them while one is open.
const narrow = () => window.matchMedia("(max-width:600px)").matches;
map.on("popupopen", () => { if (narrow()) document.querySelectorAll(".panel").forEach(p => p.style.display = "none"); });
map.on("popupclose", () => document.querySelectorAll(".panel").forEach(p => p.style.display = ""));

document.getElementById("search").addEventListener("change", e => {
  const q = e.target.value.trim().toLowerCase(); if (!q) return;
  const hit = markers.find(([c]) => c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q));
  if (hit) { map.setView(hit[1].getLatLng(), 13); hit[1].openPopup(); }
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
