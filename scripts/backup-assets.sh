#!/bin/sh
# Copy dex's generated material to the external drive.
#
# `assets/` and `datasets/` are not in git — they are large binaries that churn
# with every run — so this copy is the only one that is not on the boot disk.
#
# Run by cron; see the schedule in README.md.
set -eu

REPO="/Users/raduparadovschi/dex"
VOLUME="/Volumes/external"
DEST="$VOLUME/dex"
LOG="$REPO/.pm2-logs/backup-assets.log"

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# The guard that matters. Without the drive mounted, /Volumes/external is an
# ordinary empty directory on the boot disk, and rsync would happily fill it
# with a gigabyte of video — filling the disk while appearing to succeed.
if ! /sbin/mount | grep -q " on $VOLUME "; then
  say "skipped: $VOLUME is not mounted"
  exit 0
fi

mkdir -p "$DEST"
cd "$REPO"

# No --delete: this is additive. A backup that mirrors deletions will happily
# erase its own copy the day something goes wrong locally, and that is the day
# it is needed. The cost is that renamed packages leave their old names behind.
if err=$(/usr/bin/rsync -a assets datasets "$DEST/" 2>&1); then
  say "copied: $(find assets datasets -type f | wc -l | tr -d ' ') files -> $DEST"
else
  say "FAILED: $err"
  # macOS gates external drives behind TCC, and a scheduled job has no way to
  # ask. Run by hand it works, because the terminal already has the grant —
  # which is exactly what makes this failure confusing without the hint.
  case "$err" in
    *"not permitted"*)
      say "  -> grant Full Disk Access to /bin/sh in System Settings >"
      say "     Privacy & Security, then: launchctl kickstart gui/\$(id -u)/com.dex.backup-assets"
      ;;
  esac
  exit 1
fi
