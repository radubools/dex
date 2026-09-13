# The image is built here and pushed; the instance pulls it. Building on the
# instance would mean shipping the source up and paying for the build on every
# replacement.

resource "aws_ecr_repository" "dex" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true # so `terraform destroy` is not blocked by images

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "dex" {
  repository = aws_ecr_repository.dex.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 3 images; older ones are just storage."
      selection    = { tagStatus = "any", countType = "imageCountMoreThanN", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

data "aws_caller_identity" "me" {}
