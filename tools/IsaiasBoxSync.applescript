-- Copies new Hurricane Isaias frames from S3 into Box Drive. Wrapped as an app so macOS can ask
-- once for permission to write into Box Drive; launchd opens it hourly.
set syncScript to "/Users/ehsankahrizi/Developer/Isaias_Camera_Capture/tools/sync_to_box.sh"
set logf to "/Users/ehsankahrizi/Library/Logs/isaias-box-sync.log"
do shell script quoted form of syncScript & " >> " & quoted form of logf & " 2>&1 || true"
