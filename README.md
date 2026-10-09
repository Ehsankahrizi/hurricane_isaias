# Isaias camera capture

Hourly camera frames for **Hurricane Isaias (AL092026)**, **Fri Oct 9 – Sun Oct 11 2026** (CDT, the landfall area's local time). This is separate from the TWL camera cross-validation project: its own code, S3 bucket and ECS service.

## What it captures

- **Area:** cameras inside the NHC forecast cone (advisory 10, `data/cone.geojson`, from `AL092026_CONE_latest.kmz`) **and** within 50 km of the coast. The coast is the 1,431 HTF stations (`data/htf_stations.json`), the same rule as the coastal camera map.
- **Cameras** (found at start-up and again every 6 h; cameras found earlier are kept):
  - traffic cameras from Road511, which here are all Florida DOT;
  - USGS HIVIS cameras that sent an image in the last 7 days (none in this area on Oct 9);
  - Windy webcams.
  - On Oct 9 that was 233 cameras: 115 traffic and 118 Windy.
- **When:** one frame per camera every hour on the hour, from 00:00 CDT Friday to 23:00 CDT Sunday (72 hours). A frame identical to the camera's previous one (a frozen camera) is not saved again.

## Where the frames go

```
s3://bil6-isaias-camera-capture-858933856877/
  captures/<source>_<camera id>/<UTC stamp>.jpg   e.g. traffic_FL-cam-3800/2026-10-09T05-21Z.jpg
  captures/cameras.csv                            one row per camera: name, location, distance to coast, frame count
  state/manifest.json                             cameras and every frame (fetch time, image time, age, sha1)
  config/windy_api_key                            private (copied from the TWL bucket)
  code/isaias_capture.zip                         the code the ECS task runs
```

File names are the fetch time in **UTC** (CDT = UTC − 5 h).

## Box copy (lab Mac)

`captures/` is copied every hour into Box at `CamerData/Hurricane Isaias/Captures`, one folder per camera plus `cameras.csv`.

- **How it runs:** launchd job `com.ehsankahrizi.isaias-box-sync` (`tools/com.ehsankahrizi.isaias-box-sync.plist`, installed in `~/Library/LaunchAgents`) opens `~/Developer/IsaiasBoxSync.app` hourly. The app runs `tools/sync_to_box.sh` (`aws s3 sync`).
- **Why an app:** macOS lets a background job write into Box Drive only as an app that has been granted access. Its source is `tools/IsaiasBoxSync.applescript`.
- **Log:** `~/Library/Logs/isaias-box-sync.log`
- **Run it now:** `open -g ~/Developer/IsaiasBoxSync.app`
- **Remove it after the storm:** `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.ehsankahrizi.isaias-box-sync.plist`, then delete the plist and the app.

## AWS (BIL6 account, us-east-2)

`deploy.sh` creates and removes everything; all names start with `bil6-isaias-camera-capture`.

| Resource | Name |
|---|---|
| ECS cluster / service / task definition | `bil6-isaias-camera-capture-cluster` / `bil6-isaias-camera-capture` / `bil6-isaias-camera-capture` (1 task, 0.25 vCPU / 0.5 GB) |
| IAM roles | `…-exec` (write logs), `…-task` (its own bucket only, and setting its own service to 0 tasks) |
| Security group | `…-sg` (no inbound rules) |
| Logs | `/ecs/bil6-isaias-camera-capture` (30 days) |

- **Image:** public `python:3.12-slim`. The task installs `requests`/`boto3` and downloads the code from the bucket at start-up, so there is no image to build.
- **Cost:** about $0.02/hour, so about $1.50 for the three days.
- **Stopping:** after Sun Oct 11 24:00 CDT the task sets its own service to 0 tasks.

| Task | Command |
|---|---|
| Watch the logs | `aws logs tail /ecs/bil6-isaias-camera-capture --follow` |
| Deploy changed code | `./deploy.sh code` |
| Stop now | `aws ecs update-service --cluster bil6-isaias-camera-capture-cluster --service bil6-isaias-camera-capture --desired-count 0` |
| Remove everything except the frames | `./deploy.sh teardown` |
| Run one cycle locally | `.venv/bin/python isaias_capture.py once` (or `cameras` to list them) |

To make a local venv: `/opt/homebrew/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

**Do not run the local `run` loop while the ECS service is running.** Both update `state/manifest.json`.
