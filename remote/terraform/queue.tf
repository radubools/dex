# The queue: one topic. The page publishes to it directly with the signed-in
# person's Google token; who may publish is the topic's IAM policy, which
# local dex keeps equal to the people it grants a project to (remote.py,
# `topic_publishers`). Each local dex makes its own per-project subscription,
# filtered on the `project` attribute, so nothing else is declared here.

resource "google_pubsub_topic" "tasks" {
  name                       = "dex-tasks"
  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.enabled]
  # No google_pubsub_topic_iam_* here: the publishers are local dex's to set.
}

# --- local dex ---------------------------------------------------------------
# One identity for every machine that runs remote tasks, with full read/write
# on the three services; its key is made by ../setup-local.sh with gcloud, so
# it never sits in Terraform state.

resource "google_service_account" "local" {
  account_id   = "dex-local"
  display_name = "dex remote: local dex bridge"
  depends_on   = [google_project_service.enabled]
}

resource "google_project_iam_member" "local" {
  # pubsub.admin: it makes and pulls its per-project subscriptions and sets
  #   the topic's publishers.
  # datastore.user: it reads and writes every Firestore document.
  for_each = toset(["roles/pubsub.admin", "roles/datastore.user"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.local.email}"
}

resource "google_storage_bucket_iam_member" "local_files" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.local.email}"
}
