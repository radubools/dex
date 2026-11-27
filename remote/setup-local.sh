#!/usr/bin/env bash
# Give this machine's dex what it needs to take remote tasks:
#   ~/.config/dex/remote.json    which project, bucket and topic
#   ~/.config/dex/remote-sa.json the dex-local service account's key
#
# Run it here (with gcloud signed in), then copy both files to every other
# machine that should serve remote tasks (e.g. the Mac Studio):
#   scp ~/.config/dex/remote*.json studio:.config/dex/
# dex picks them up at its next start; then turn a project on in Settings.
set -euo pipefail
project="${DEX_REMOTE_PROJECT:-dex-remote}"
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
dir="$HOME/.config/dex"
mkdir -p "$dir"
chmod 700 "$dir"

account="dex-local@${project}.iam.gserviceaccount.com"
if [ -f "$dir/remote-sa.json" ]; then
  echo "keeping the existing key in $dir/remote-sa.json"
else
  gcloud iam service-accounts keys create "$dir/remote-sa.json" --iam-account "$account" --project "$project"
  chmod 600 "$dir/remote-sa.json"
fi

cat > "$dir/remote.json" <<JSON
{
  "gcpProject": "${project}",
  "bucket": "${project}-files",
  "topic": "dex-tasks",
  "credentials": "remote-sa.json"
}
JSON
chmod 600 "$dir/remote.json"
echo "wrote $dir/remote.json"
