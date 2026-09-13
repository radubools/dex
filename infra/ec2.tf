data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.1-${var.architecture}"]
  }
}

# A stable address, because CloudFront's origin is this instance's public DNS
# name and that name is derived from the IP. Without it, a stop/start would
# silently point CloudFront at nothing.
resource "aws_eip" "app" {
  domain = "vpc"
  tags   = { Name = local.name }
}

resource "aws_eip_association" "app" {
  instance_id   = aws_instance.app.id
  allocation_id = aws_eip.app.id
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # No key_name: there is no SSH ingress, and SSM gives a shell without one.

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    # IMDSv2 only. v1 lets any process that can make an HTTP request read the
    # instance role's credentials.
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # containers are one hop further out
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region       = var.region
    param_prefix = "/${local.name}"
    registry     = split("/", aws_ecr_repository.dex.repository_url)[0]
    image        = "${aws_ecr_repository.dex.repository_url}:latest"
    dex_project  = var.dex_project
    dex_effort   = var.dex_effort
  })

  # Re-bootstrap when the script changes, rather than leaving a live instance
  # running a version of it nobody has.
  user_data_replace_on_change = true

  tags = { Name = local.name }

  depends_on = [
    aws_ssm_parameter.claude_token,
    aws_ssm_parameter.dex_token,
    aws_ssm_parameter.postgres_password,
  ]
}
