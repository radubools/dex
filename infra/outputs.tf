output "url" {
  description = "The public HTTPS endpoint. No domain needed; CloudFront supplies the name and the certificate."
  value       = "https://${aws_cloudfront_distribution.dex.domain_name}"
}

output "instance_id" {
  description = "For a shell: aws ssm start-session --target <this>"
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "The instance's stable address. Not reachable directly — the security group admits CloudFront only."
  value       = aws_eip.app.public_ip
}

output "ecr_repository_url" {
  description = "Push the image here; the instance pulls from it."
  value       = aws_ecr_repository.dex.repository_url
}

output "param_prefix" {
  description = "Where the secrets live. Set them with `aws ssm put-parameter --overwrite`."
  value       = "/${local.name}"
}

output "next_steps" {
  description = "The order these have to happen in."
  value       = <<-EOT

    1. Get a subscription token, on THIS machine (it needs a browser):
         claude setup-token
       EC2 is headless, so the OAuth redirect cannot complete there. The token
       is long-lived, so this is a one-off.

    2. Put it, and an API secret of your choosing, into Parameter Store:
         aws ssm put-parameter --overwrite --region ${var.region} \
           --name /${local.name}/CLAUDE_CODE_OAUTH_TOKEN --type SecureString --value '<token>'
         aws ssm put-parameter --overwrite --region ${var.region} \
           --name /${local.name}/DEX_TOKEN --type SecureString --value "$(openssl rand -hex 24)"

    3. Build and push the image:
         aws ecr get-login-password --region ${var.region} \
           | docker login --username AWS --password-stdin ${split("/", aws_ecr_repository.dex.repository_url)[0]}
         docker build -t ${aws_ecr_repository.dex.repository_url}:latest ..
         docker push ${aws_ecr_repository.dex.repository_url}:latest

    4. Bootstrap the instance now that the image and secrets exist:
         aws ssm start-session --target ${aws_instance.app.id}
         sudo cat /var/log/dex-bootstrap.log     # watch it, or re-run:
         sudo bash -c 'cd /opt/dex && docker compose pull && docker compose up -d'

    5. Open ${aws_cloudfront_distribution.dex.domain_name} over https. A new
       distribution takes a few minutes to deploy before it answers.

  EOT
}
