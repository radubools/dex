# CloudFront is what makes this public over HTTPS without a domain: the
# distribution comes with its own `*.cloudfront.net` name and a valid
# certificate for it. An ALB would need a certificate, and ACM will not issue
# one for a name you do not control.

locals {
  origin_id = "dex-ec2"
}

# Nothing here is cacheable: every path is either the API, an SSE stream, or a
# hashed bundle that is already immutable. A cache in front of the API would
# serve one operator another's task list.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# Forward everything to the origin. dex authenticates with a header and the UI
# needs its cookies and query strings intact.
data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "dex" {
  enabled         = true
  comment         = local.name
  is_ipv6_enabled = true

  origin {
    # The EIP's public DNS name, not the IP: CloudFront requires a domain for a
    # custom origin, and this one is stable as long as the EIP is.
    domain_name = "ec2-${replace(aws_eip.app.public_ip, ".", "-")}.${var.region == "us-east-1" ? "compute-1" : "${var.region}.compute"}.amazonaws.com"
    origin_id   = local.origin_id

    custom_origin_config {
      http_port  = 4318
      https_port = 443
      # HTTP to the origin. The hop is inside AWS and the security group admits
      # only CloudFront; terminating TLS at the origin would need a certificate
      # for a name we do not own, which is the problem we are solving.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # dex streams SSE and a task can think for minutes without emitting.
      # The default 30s read timeout would cut the stream repeatedly.
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id = local.origin_id
    # The whole point of the distribution: viewers get HTTPS whether they ask
    # for it or not.
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = false # gzip buffers, and buffering stalls SSE
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  # PriceClass_100 is North America and Europe. The cheapest class, and this is
  # one operator's tool rather than a global site.
  price_class = "PriceClass_100"

  viewer_certificate {
    # CloudFront's own certificate for *.cloudfront.net. No domain required.
    cloudfront_default_certificate = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = { Name = local.name }
}
