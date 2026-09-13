# dex on AWS

One EC2 instance behind CloudFront, which supplies a public HTTPS endpoint and
a certificate without your owning a domain.

```
  you ──https──▶ CloudFront  d1234abcd.cloudfront.net
                     │        (its own cert; CachingDisabled; AllViewer)
                     │ http, port 4318
                     ▼
              EC2 m7i-flex.large + Elastic IP
              SG: CloudFront's managed prefix list ONLY. No SSH, no key pair.
                     │
              docker compose
                ├─ app       pm2: dex-api :4317 + dex-web :4318
                └─ postgres  (never published)
              volumes: postgres-data, dex-assets, dex-state

  secrets:  SSM Parameter Store  /dex-<suffix>/*   (SecureString)
  image:    ECR  dex-<suffix>:latest
  shell:    aws ssm start-session   (no inbound port)
```

## Cost, before anything else

`m7i-flex.large` is **not** in the free tier. Priced from the AWS API for
us-east-1:

| | Hourly | Monthly |
|---|---|---|
| m7i-flex.large (2 vCPU, 8 GiB) | $0.0958 | **~$70** |
| EBS 50 GB gp3 | — | ~$4 |
| Elastic IP (while associated) | — | $0 |
| CloudFront PriceClass_100 | — | ~$0 at this volume |

The 12-month free tier covers `t2.micro`/`t3.micro` only, and 1 GiB cannot run
dex: one agent subprocess measures ~300 MB and the two containers ~170 MB
before any task starts. There is no free-tier instance that fits. Run
`terraform destroy` when you are not using it — everything here is disposable
except the volumes.

## Using your Claude subscription, not an API key

The Claude CLI **cannot be a separate container.** `claude_agent_sdk` spawns it
as a subprocess of the dex process and talks to it over stdio, so there is
nothing for a sidecar to listen on. It lives inside the dex image, and the
image build fails if it is missing.

What travels instead is a token. `claude setup-token` performs the browser
OAuth flow and returns a **long-lived** subscription token, which
`dex.runner.credential_source()` already accepts as `CLAUDE_CODE_OAUTH_TOKEN`.

Run it **on your own machine**, not on the instance: EC2 is headless, so the
OAuth redirect has nowhere to land. It is a one-off.

```bash
claude setup-token          # browser opens; copy the token it prints
```

Then put it where the instance can read it:

```bash
aws ssm put-parameter --overwrite --region us-east-1 \
  --name /dex-<suffix>/CLAUDE_CODE_OAUTH_TOKEN --type SecureString --value '<token>'
```

`terraform output param_prefix` gives the exact prefix.

The token is deliberately **not** a Terraform variable: a value passed in
tfvars is written into the state file in plain text. Terraform creates each
parameter with a placeholder and `ignore_changes = [value]`, so your real
secret never enters state and a later `apply` will not clobber it.

## Order of operations

Terraform brings up the infrastructure, but the instance cannot start dex until
the image and the secrets exist. So: apply, then fill those in, then bootstrap.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply                       # ~22 resources; CloudFront takes a few minutes
terraform output next_steps           # prints the rest, with your real names filled in
```

Then, in order:

1. **Secrets** — the Claude token as above, plus an API secret of your own:

   ```bash
   aws ssm put-parameter --overwrite --region us-east-1 \
     --name /dex-<suffix>/DEX_TOKEN --type SecureString --value "$(openssl rand -hex 24)"
   ```

   `DEX_TOKEN` is not optional here. Empty means the API has no authentication,
   and dex queues tasks that run code on your Claude account.

2. **Image** — built here and pushed; the instance pulls it:

   ```bash
   aws ecr get-login-password --region us-east-1 \
     | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
   docker build -t <ecr-url>:latest ..
   docker push <ecr-url>:latest
   ```

3. **Bootstrap** — the instance's first boot ran before the image existed, so
   tell it to try again:

   ```bash
   aws ssm start-session --target <instance-id>
   sudo tail -f /var/log/dex-bootstrap.log
   sudo bash -c 'cd /opt/dex && docker compose pull && docker compose up -d'
   ```

4. Open the `url` output over HTTPS.

## Getting a shell

There is no SSH: no port 22 rule and no key pair. Session Manager gives you a
shell through the instance role, with no inbound port to attack and an IAM
audit trail.

```bash
aws ssm start-session --target $(terraform output -raw instance_id)
```

Needs the Session Manager plugin locally (`brew install --cask session-manager-plugin`).

## The SSE risk, and the fallback

dex streams events over SSE heavily — measured at ~3,000 events in 5 seconds
with six tasks running. CloudFront passes streaming responses through, and the
behaviour is configured for it: caching disabled, `compress = false` (gzip
buffers, and buffering stalls a stream), and a 60s origin read timeout so a
task that thinks for a minute without emitting does not get cut off.

If the UI still feels laggy or the thinking stream stutters, CloudFront is the
first suspect. The fallback is to skip it: run Caddy on the instance with a
Let's Encrypt certificate for a `sslip.io` name — `dex.<dashed-ip>.sslip.io`
resolves to the Elastic IP, so you get real HTTPS pointed straight at the
instance with no domain and no CDN in the path. That trades an AWS-only
topology for a third-party DNS dependency, which is why it is not the default.

## What is deliberately closed

- **No inbound 22.** Shell is SSM.
- **HTTP 4318 only from CloudFront's managed prefix list**, so the origin is
  not reachable directly even though it has a public IP. "Nobody knows the URL"
  is not an access control.
- **Postgres is never published** — not to the host, not to the VPC edge.
- **IMDSv2 required**, with a hop limit of 2 so containers can still reach it.
  IMDSv1 lets anything that can make an HTTP request read the instance role.
- **The secrets policy is scoped** to this deployment's own parameter prefix,
  and `kms:Decrypt` is conditioned on `ViaService = ssm`.
- **EBS encrypted**, and the `.env` on the instance is written with `umask 077`.

## Tearing it down

```bash
terraform destroy
```

Takes everything, including the ECR images (`force_delete`) and the Docker
volumes with the instance. Nothing survives, so copy anything out first:

```bash
aws ssm start-session --target <instance-id>
sudo docker run --rm -v dex_dex-assets:/a -v /tmp:/out alpine tar czf /out/assets.tgz -C /a .
```

CloudFront takes several minutes to delete; that is normal.
