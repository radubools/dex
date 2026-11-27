#!/usr/bin/env bash
# Build the page and apply the Terraform. Re-run after any change to either.
#
#   remote/deploy.sh                 # plan and apply
#   remote/deploy.sh -auto-approve   # without the prompt
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
project="${DEX_REMOTE_PROJECT:-dex-remote}"
export PATH="$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"

# Terraform authenticates as whoever gcloud is signed in as.
GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
export GOOGLE_OAUTH_ACCESS_TOKEN

# What Terraform itself needs before it can enable the rest.
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
  storage.googleapis.com --project "$project"

if ! gcloud storage buckets describe "gs://${project}-tfstate" --project "$project" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${project}-tfstate" --project "$project" \
    --location=us-central1 --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets update "gs://${project}-tfstate" --versioning
fi

(cd "$here/web" && npm ci --no-audit --no-fund >/dev/null && npm run build)

cd "$here/terraform"
terraform init -input=false -reconfigure -backend-config="bucket=${project}-tfstate" >/dev/null
# Firebase is added to the project in the console (its terms need a person);
# adopt it rather than trying to create it again.
if ! terraform state list 2>/dev/null | grep -q '^google_firebase_project.this$'; then
  terraform import -input=false -var "project_id=${project}" google_firebase_project.this "projects/${project}" \
    >/dev/null 2>&1 || echo "Firebase is not on ${project} yet: see remote/README.md, First deploy." >&2
fi
terraform apply -input=false -var "project_id=${project}" "$@"

# For `npm run dev` in remote/web: the same config the bucket serves.
gcloud storage cat "gs://${project}-web/config.json" > "$here/web/public/config.json"
echo
echo "Page: $(terraform output -raw url)"
