# A VPC of its own rather than the default one, so `terraform destroy` takes
# everything with it and nothing here can collide with other work in the
# account.

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  name = "dex-${random_id.suffix.hex}"
}

resource "aws_vpc" "main" {
  cidr_block         = "10.20.0.0/16"
  enable_dns_support = true
  # Required: CloudFront's origin is the instance's public DNS name, which only
  # exists when the VPC hands out hostnames.
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The instance accepts HTTP only from CloudFront. AWS publishes the edge ranges
# as a managed prefix list, so this stays correct as they add edges — far
# better than 0.0.0.0/0 with "nobody knows the URL" as the control.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "dex app: HTTP from CloudFront only; no SSH (shell is via SSM)"
  vpc_id      = aws_vpc.main.id

  # No ingress rule for 22. There is no key pair either: a shell comes from
  # SSM Session Manager, which needs no open port and is logged by IAM.
  ingress {
    description     = "UI from CloudFront edges"
    from_port       = 4318
    to_port         = 4318
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  dynamic "ingress" {
    # Only created when admin_cidr is set, so the default really is "closed".
    for_each = var.admin_cidr == "" ? [] : [var.admin_cidr]
    content {
      description = "API direct, for debugging"
      from_port   = 4317
      to_port     = 4317
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  egress {
    description = "Pull images, reach Anthropic, apt/npm"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-app" }
}
