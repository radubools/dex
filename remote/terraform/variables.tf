variable "project_id" {
  description = "The Google Cloud project dex-remote lives in."
  type        = string
  default     = "dex-remote"
}

variable "region" {
  description = "Region for the workflow, the trigger and both buckets. Must also be a Firestore location."
  type        = string
  default     = "us-central1"
}

variable "google_oauth_client_id" {
  description = <<-EOT
    A Web OAuth client for "Sign in with Google", with
    https://<project_id>.firebaseapp.com/__/auth/handler as an authorised redirect URI.
    Google offers no API to create one, so it is made once in the console
    (APIs & Services → Credentials). Empty: enable Google in the Firebase
    console's Authentication page instead, which makes the client itself.
  EOT
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "extra_authorized_domains" {
  description = "Further origins allowed to sign in, e.g. a custom domain in front of the web bucket."
  type        = list(string)
  default     = []
}

variable "web_dist" {
  description = "The built page (npm run build in ../web)."
  type        = string
  default     = "../web/dist"
}

variable "files_retention_days" {
  description = "Delete uploaded results and attachments this many days after upload; 0 keeps them. Local dex keeps its own copy either way."
  type        = number
  default     = 0
}

variable "web_app_dist" {
  description = "The built application bundle (npm run build in ../web), served only to authorised users."
  type        = string
  default     = "../web/dist-app"
}
