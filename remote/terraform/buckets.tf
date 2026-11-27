# Two buckets: the page (public, it holds no data) and the files (private:
# people download results and upload attachments through Firebase Storage
# rules; local dex reads and writes everything with its service account).

resource "google_storage_bucket" "web" {
  name                        = "${var.project_id}-web"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  force_destroy               = true
  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }
  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "web_public" {
  bucket = google_storage_bucket.web.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

resource "google_storage_bucket" "files" {
  name                        = "${var.project_id}-files"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Storage is the one cost that grows without bound (see ../README.md).
  dynamic "lifecycle_rule" {
    for_each = var.files_retention_days > 0 ? [1] : []
    content {
      condition { age = var.files_retention_days }
      action { type = "Delete" }
    }
  }
  # The page downloads with the Firebase SDK (getBlob), a cross-origin fetch.
  cors {
    origin          = ["https://storage.googleapis.com", "http://localhost:4419", "https://${var.project_id}.web.app"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Length", "Content-Disposition", "Authorization"]
    max_age_seconds = 3600
  }
  depends_on = [google_project_service.enabled]
}

# --- the page ------------------------------------------------------------------

locals {
  content_types = {
    html = "text/html; charset=utf-8", js = "text/javascript", css = "text/css",
    json = "application/json", svg = "image/svg+xml", png = "image/png",
    ico  = "image/x-icon", woff2 = "font/woff2", map = "application/json", txt = "text/plain",
  }
  web_files = fileset(var.web_dist, "**")
}

resource "google_storage_bucket_object" "web" {
  for_each       = local.web_files
  bucket         = google_storage_bucket.web.name
  name           = each.value
  source         = "${var.web_dist}/${each.value}"
  detect_md5hash = filemd5("${var.web_dist}/${each.value}")
  content_type   = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
  # index.html must never be stale; the hashed assets can be cached for good.
  cache_control = endswith(each.value, ".html") ? "no-cache" : "public, max-age=31536000, immutable"
}

# What the page needs to find Firebase, read at runtime so a rebuild is not
# needed when the project changes.
resource "google_storage_bucket_object" "config" {
  bucket        = google_storage_bucket.web.name
  name          = "config.json"
  content_type  = "application/json"
  cache_control = "no-cache"
  content = jsonencode({
    firebase = {
      apiKey            = data.google_firebase_web_app_config.remote.api_key
      authDomain        = data.google_firebase_web_app_config.remote.auth_domain
      projectId         = var.project_id
      storageBucket     = google_storage_bucket.files.name
      appId             = google_firebase_web_app.remote.app_id
      messagingSenderId = data.google_firebase_web_app_config.remote.messaging_sender_id
    }
    topic       = google_pubsub_topic.tasks.id
    pollSeconds = 15
  })
}

# --- the application -----------------------------------------------------------
# Not in the public bucket: the shell fetches it through Firebase Storage once
# the person is authorised (storage.rules, match /app/{file}).
resource "google_storage_bucket_object" "app" {
  bucket         = google_storage_bucket.files.name
  name           = "app/app.js"
  source         = "${var.web_app_dist}/app.js"
  detect_md5hash = filemd5("${var.web_app_dist}/app.js")
  content_type   = "text/javascript"
  cache_control  = "no-cache"
}
