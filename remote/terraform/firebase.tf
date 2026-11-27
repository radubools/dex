# Firebase on the existing project. Adding Firebase needs its terms accepted
# once by a person, in the console (README: "First deploy"); deploy.sh then
# imports the project rather than creating it.
#
# Firebase on the existing project: Auth (Google sign-in), Firestore (requests,
# task mirrors, access), and Storage rules over the files bucket.

resource "google_firebase_project" "this" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.enabled]
}

resource "google_firebase_web_app" "remote" {
  provider     = google-beta
  project      = var.project_id
  display_name = "dex remote"
  depends_on   = [google_firebase_project.this]
}

data "google_firebase_web_app_config" "remote" {
  provider   = google-beta
  project    = var.project_id
  web_app_id = google_firebase_web_app.remote.app_id
}

# --- Auth --------------------------------------------------------------------

resource "google_identity_platform_config" "auth" {
  provider = google-beta
  project  = var.project_id
  # Who may open the sign-in popup: the web bucket's host, Firebase's own
  # handler domain, and localhost for `npm run dev`.
  authorized_domains = concat([
    "localhost",
    "storage.googleapis.com",
    "${var.project_id}.firebaseapp.com",
    "${var.project_id}.web.app",
  ], var.extra_authorized_domains)
  sign_in {
    allow_duplicate_emails = false
  }
  depends_on = [google_firebase_project.this]
}

resource "google_identity_platform_default_supported_idp_config" "google" {
  count         = var.google_oauth_client_id == "" ? 0 : 1
  provider      = google-beta
  project       = var.project_id
  enabled       = true
  idp_id        = "google.com"
  client_id     = var.google_oauth_client_id
  client_secret = var.google_oauth_client_secret
  depends_on    = [google_identity_platform_config.auth]
}

# --- Firestore ---------------------------------------------------------------

resource "google_firestore_database" "default" {
  provider    = google-beta
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  # Firestore holds the only copy of a request until a dex takes it.
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  depends_on              = [google_firebase_project.this]
}

resource "google_firebaserules_ruleset" "firestore" {
  provider = google-beta
  project  = var.project_id
  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/firestore.rules")
    }
  }
  depends_on = [google_firestore_database.default]
}

resource "google_firebaserules_release" "firestore" {
  provider     = google-beta
  project      = var.project_id
  name         = "cloud.firestore"
  ruleset_name = google_firebaserules_ruleset.firestore.name
  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.firestore]
  }
}

# --- Storage -----------------------------------------------------------------

resource "google_firebase_storage_bucket" "files" {
  provider   = google-beta
  project    = var.project_id
  bucket_id  = google_storage_bucket.files.name
  depends_on = [google_firebase_project.this]
}

resource "google_firebaserules_ruleset" "storage" {
  provider = google-beta
  project  = var.project_id
  source {
    files {
      name    = "storage.rules"
      content = file("${path.module}/storage.rules")
    }
  }
  depends_on = [google_firebase_storage_bucket.files]
}

resource "google_firebaserules_release" "storage" {
  provider     = google-beta
  project      = var.project_id
  name         = "firebase.storage/${google_storage_bucket.files.name}"
  ruleset_name = google_firebaserules_ruleset.storage.name
  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.storage]
  }
}

# The storage rules read `access/<email>` from Firestore; Firebase Storage's
# own service agent needs leave to.
resource "google_project_service_identity" "firebasestorage" {
  provider = google-beta
  project  = var.project_id
  service  = "firebasestorage.googleapis.com"
}

resource "google_project_iam_member" "storage_reads_firestore" {
  project = var.project_id
  role    = "roles/firebaserules.firestoreServiceAgent"
  member  = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-firebasestorage.iam.gserviceaccount.com"
  # The agent exists once a bucket is linked, not when the identity is asked for.
  depends_on = [google_project_service_identity.firebasestorage, google_firebase_storage_bucket.files]
}
