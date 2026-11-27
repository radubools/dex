# State lives in the project itself, so either machine can apply. The bucket
# is made once by ../deploy.sh (Terraform cannot create its own backend).
terraform {
  backend "gcs" {
    bucket = "dex-remote-tfstate"
    prefix = "dex-remote"
  }
}
