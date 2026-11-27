output "url" {
  description = "The page."
  value       = "https://storage.googleapis.com/${google_storage_bucket.web.name}/index.html"
}

output "local_service_account" {
  value = google_service_account.local.email
}

output "local_config" {
  description = "~/.config/dex/remote.json for every machine that serves remote tasks."
  value = {
    gcpProject  = var.project_id
    bucket      = google_storage_bucket.files.name
    topic       = google_pubsub_topic.tasks.name
    credentials = "remote-sa.json"
  }
}

output "oauth_redirect_uri" {
  description = "Add this to the Google OAuth client's authorised redirect URIs."
  value       = "https://${var.project_id}.firebaseapp.com/__/auth/handler"
}
