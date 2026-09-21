# Deploying to AWS EC2 + Cloudflare Tunnel

The runbook for the option we costed at roughly **$34/month**: one EC2 instance
in Tokyo runs the whole `docker compose` stack on weekday business hours only,
and Cloudflare fronts it through a Tunnel.

Compute is the only thing that scales with uptime, so stopping the instance
overnight and at weekends is what makes this cheap: 264 billed hours a month
instead of 730.

```
Cloudflare zone: kensaki-vn.com
  ├─ company.kensaki-vn.com      ─┐
  ├─ api-company.kensaki-vn.com  ─┼─ Tunnel (outbound only) ─→ VPS ─→ compose stack
  └─ ai-company.kensaki-vn.com   ─┘
```

All three hostnames sit one level under the zone on purpose: Cloudflare's free
Universal SSL covers `kensaki-vn.com` and `*.kensaki-vn.com`, but a wildcard
matches a single label. `app.company.kensaki-vn.com` would not be covered and
would require Advanced Certificate Manager ($10/month).

Nothing listens on a public port. `cloudflared` runs inside the compose network
and dials out to Cloudflare, so the VPS firewall only ever allows SSH.

## What you need before starting

| | |
| --- | --- |
| Domain | `kensaki-vn.com` - already on Cloudflare (tony/kiki.ns.cloudflare.com) |
| Cloudflare account | Free plan is enough; Zero Trust enabled for Tunnels |
| AWS account | With permission to run EC2, and an EC2 key pair in `ap-northeast-1` |
| API keys | `GOOGLE_AI_API_KEY` (Gemini), `JINA_API_KEY` (reranking) |

`t4g.large` is Graviton, and every image in the stack is a native `linux/arm64`
build - `ankane/pgvector` included - so nothing runs under emulation.

8 GB of RAM is sized for the ai-service image build (torch), not for running it.
The stack idles at about 560 MB total across all six containers.

## 1. Create the instance

```bash
aws sso login
KEY_NAME=<your-ec2-key-pair> ./deploy/aws/provision-ec2.sh
```

Resolves the current Ubuntu 24.04 arm64 AMI, creates a security group that
allows SSH from your address only, and launches a `t4g.large` with an encrypted
80 GB gp3 root volume. Re-running it reuses what already exists.

No Elastic IP is allocated. The tunnel dials out, so the instance never needs a
stable inbound address, and the public IP changing on each start costs nothing.

## 2. Bootstrap the instance

```bash
ssh ubuntu@<public-ip> 'sudo bash -s' < deploy/bootstrap-vps.sh
```

Installs Docker, creates a `deploy` user carrying your SSH key, adds 4 GB of
swap, restricts the firewall to SSH, and turns off SSH password login.

## 3. Schedule the weekday hours

```bash
./deploy/aws/schedule-start-stop.sh <instance-id>
```

Creates two EventBridge schedules - start 09:00, stop 21:00, Monday to Friday,
Asia/Tokyo - plus the IAM role they assume. Override with `START_CRON`,
`STOP_CRON` or `TZ_NAME`.

Every compose service is `restart: always`, so the stack comes back up by itself
when the instance boots. Nothing needs to run on login.

## 4. Create the Cloudflare Tunnel

Zero Trust dashboard → **Networks → Tunnels → Create a tunnel** → type
`cloudflared` → name it `ai-workforce`. Copy the **token** from the install
command; that is the only credential the VPS needs.

Then add three public hostnames to the tunnel:

| Public hostname | Service | Why |
| --- | --- | --- |
| `company.kensaki-vn.com` | `http://frontend:3000` | The web UI |
| `api-company.kensaki-vn.com` | `http://backend:8000` | The browser calls it directly (`NEXT_PUBLIC_API_URL`) |
| `ai-company.kensaki-vn.com` | `http://ai-service:8100` | The knowledge pipeline page streams from it directly |

Service targets use compose service names because `cloudflared` shares the
stack's network. DNS records are created automatically by Cloudflare.

`ai-company` cannot be skipped: `frontend/app/knowledge/pipeline/page.tsx`
opens an SSE stream straight from the browser to the ai-service.

The refresh cookie uses `SameSite=lax`, which is fine here because all three
hostnames share the `kensaki-vn.com` registrable domain and therefore count as
the same site.

## 5. Configure the zone

- **SSL/TLS → Overview**: Full (strict)
- **SSL/TLS → Edge Certificates**: Always Use HTTPS on
- **Caching → Cache Rules**: bypass cache when hostname is
  `api-company.kensaki-vn.com` or `ai-company.kensaki-vn.com`
- **Security → Bots**: Bot Fight Mode **off** for those two hostnames (it breaks
  SSE and XHR)
- **Security → WAF**: add a rate limit on `/api/v1/auth/login`

## 6. Fill in the environment files

On the instance, as the `deploy` user:

```bash
git clone <repo> ai-workforce && cd ai-workforce
cp deploy/env.prod.example .env
cp deploy/backend.env.prod.example backend/.env
```

Generate every secret fresh:

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "AI_SERVICE_INTERNAL_TOKEN=$(openssl rand -hex 32)"
```

`AI_SERVICE_INTERNAL_TOKEN` must be identical in `.env` and `backend/.env`.
`GOOGLE_AI_API_KEY` and `OPENAI_API_KEY` go in **`backend/.env`** only — compose
passes them to the ai-service through `env_file`, so putting them in the root
`.env` has no effect.

## 7. Deploy

```bash
./deploy/deploy.sh
```

Builds the images, starts the stack, waits for the backend healthcheck, and runs
`alembic upgrade head`. The script refuses to run if `APP_ENV`, `APP_DEBUG` or
any required hostname is wrong.

The first build takes 10-20 minutes (torch and the Next.js build).

## 8. Create the first account

Do **not** run `python -m app.db.init_db` — it seeds a demo tenant whose users
all share one password.

Register through the API instead. The first registration creates the workspace
and its creator becomes CEO:

```bash
curl -X POST https://api-company.kensaki-vn.com/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","full_name":"Your Name",
       "password":"<at least 8 characters>","tenant_name":"Your Company"}'
```

## Operating notes

```bash
C="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"

$C ps                          # status
$C logs -f backend ai-service  # logs
$C exec backend alembic current
./deploy/deploy.sh --pull      # pull, rebuild, restart, migrate
```

**Starting and stopping outside the schedule.**

```bash
R="--region ap-northeast-1"
aws $R ec2 start-instances --instance-ids <instance-id>
aws $R ec2 stop-instances  --instance-ids <instance-id>

# the public IP changes on every start
aws $R ec2 describe-instances --instance-ids <instance-id> \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

**While the instance is stopped, the site returns Cloudflare error 1033.** That
is the tunnel being disconnected, not a fault. It clears about a minute after
the instance boots and `cloudflared` reconnects.

**Data survives a stop.** The EBS volume keeps the `pgdata` volume and
`backend/data`, which is why the volume is still billed while stopped. Deleting
the instance deletes the volume too (`DeleteOnTermination=true`), so take a dump
first if the data matters.

**Backups.** `pgdata` holds the database and `backend/data` holds uploaded
documents. Neither is backed up by anything in this repo:

```bash
$C exec -T postgres pg_dump -U postgres ai_workforce_db | gzip > db-$(date +%F).sql.gz
tar czf knowledge-$(date +%F).tar.gz backend/data
```

**Changing a hostname requires a rebuild.** `NEXT_PUBLIC_API_URL` and
`NEXT_PUBLIC_AI_SERVICE_URL` are baked into the frontend bundle at build time
(`frontend/Dockerfile`), so editing `.env` and restarting is not enough — run
`./deploy/deploy.sh` to rebuild.

**Cloudflare times out at 100 seconds** waiting for the first response byte
(error 524). `LLM_TIMEOUT_SECONDS=85` and `AI_SERVICE_TIMEOUT_SECONDS=90` keep
the AI calls under that ceiling. Streaming endpoints are fine once the first
byte is out.

**Keep reranking on the Jina API.** Switching `AI_RERANK_BACKEND` to `bge` loads
the model locally and needs a GPU host, which costs more per month than this
entire deployment.
