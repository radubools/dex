variable "region" {
  description = "AWS region. us-east-1 by default: CloudFront's own certificate needs no regional cert, and EC2 is cheapest here."
  type        = string
  default     = "us-east-1"
}

variable "architecture" {
  description = <<-EOT
    Must match the image you push. Docker builds for the machine it runs on, so
    a build on an Apple Silicon Mac produces arm64 — and an arm64 image on an
    x86_64 instance dies with `exec format error`, which is what the first
    version of this plan would have done.

    arm64 is also cheaper: t4g.large is $49/mo against m7i-flex.large's $70 for
    the same 2 vCPU / 8 GiB. Use x86_64 only if you build with
    `docker build --platform linux/amd64`, which is emulated and slow on a Mac.
  EOT
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "instance_type" {
  description = <<-EOT
    NOT free tier. Must match `architecture`: t4g/m7g/m8g are arm64,
    m7i-flex/t3 are x86_64.

    Sizing follows `Tasks at once`, because each concurrent task is a ~300 MB
    agent subprocess. Measured totals including OS, Postgres and the servers:
    concurrency 1 ~0.7 GB, 3 ~1.3 GB, 6 ~2.2 GB. 8 GiB is generous; 2-4 GiB is
    enough at low concurrency and much cheaper.
  EOT
  type        = string
  default     = "t4g.large"
}

variable "root_volume_gb" {
  description = "Root EBS size. Holds the image (~1.5 GB), Postgres, and every generated package."
  type        = number
  default     = 50
}

variable "dex_project" {
  description = "Which project a task belongs to when nothing says otherwise."
  type        = string
  default     = "algorithms"
}

variable "dex_effort" {
  description = "Thinking effort: low | medium | high | xhigh | max. Overridable live in the UI."
  type        = string
  default     = "high"
}

variable "admin_cidr" {
  description = <<-EOT
    Optional CIDR allowed to reach the API port directly, bypassing CloudFront
    — useful for curl and debugging. Empty means nobody: access is through
    CloudFront, and a shell is through SSM Session Manager, so this can stay
    empty. Set to "x.x.x.x/32" if you want it.
  EOT
  type        = string
  default     = ""
}
