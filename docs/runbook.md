# MeshWiki Operations Runbook

This document covers deployment, backup/restore, troubleshooting, security incidents, and scaling for the production MeshWiki instance.

---

## 1. Deployment

### Initial Server Setup

```bash
# Install Docker and rclone (Hetzner Ubuntu 24.04)
apt-get update && apt-get install -y docker.io rclone ufw

# Firewall: only SSH, HTTP, HTTPS
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (Caddy → HTTPS redirect)'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 443/udp comment 'HTTP/3'
ufw --force enable

# Create app directory
mkdir -p /opt/meshwiki/data/pages
cd /opt/meshwiki

# Copy deploy files from repo
cp deploy/vps/docker-compose.prod.yml .
cp deploy/vps/.env.example .env
# Edit .env with real values (see .env.example for all variables)
nano .env

# Inject domain into Caddyfile
VPS_DOMAIN=wiki.yourdomain.com envsubst < deploy/vps/Caddyfile > /opt/meshwiki/Caddyfile

# Start services
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

### Normal Deploy (CI auto-deploys on merge to main)

CI builds → pushes image to GHCR → SSHs to VPS → pulls new image → restarts container. No manual action required.

Monitor at: `https://wiki.yourdomain.com/health/live`

### Manual Deploy

```bash
ssh user@vps.yourdomain.com
cd /opt/meshwiki
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --no-deps meshwiki
```

### Rollback

Rollback to a specific image SHA (find the tag in GHCR or CI logs):

```bash
ssh user@vps.yourdomain.com
cd /opt/meshwiki
MESHWIKI_VERSION=sha-<commit-sha> docker compose -f docker-compose.prod.yml up -d --no-deps meshwiki
```

---

## 2. Backups & Restore

### Backup Schedule

Daily at 03:00 UTC via cron. Controlled by `/etc/cron.d/meshwiki-backup`:

```
0 3 * * * root BACKUP_BUCKET=s3:my-bucket /opt/meshwiki/scripts/backup.sh >> /var/log/meshwiki-backup.log 2>&1
```

Configure rclone with your S3 credentials before the first run:

```bash
rclone config  # follow prompts to add an S3/B2/Hetzner remote named to match BACKUP_BUCKET
```

### Manual Backup

```bash
BACKUP_BUCKET=s3:my-bucket BACKUP_DIR=/opt/meshwiki/data/pages /opt/meshwiki/scripts/backup.sh
```

### Restore

```bash
# List available backups
rclone ls s3:my-bucket/backups/

# Download a specific backup
rclone copy s3:my-bucket/backups/meshwiki-pages-2026-03-31.tar.gz /tmp/

# Extract to a test directory first to verify
mkdir /tmp/restore-test
tar -xzf /tmp/meshwiki-pages-2026-03-31.tar.gz -C /tmp/restore-test
ls /tmp/restore-test/pages/  # should show .md files

# Restore to production (stop the container first)
docker compose -f docker-compose.prod.yml stop meshwiki
cp -r /tmp/restore-test/pages/* /opt/meshwiki/data/pages/
docker compose -f docker-compose.prod.yml start meshwiki
```

### Verify a Backup

```bash
tar -tzf /tmp/meshwiki-pages-YYYY-MM-DD.tar.gz | head -20
# Should list pages/*.md files without errors
```

---

## 3. Troubleshooting

### Container Not Starting

```bash
docker compose -f docker-compose.prod.yml logs meshwiki --tail=50
docker compose -f docker-compose.prod.yml ps
```

Common causes:
- Missing or malformed `.env` file → check required vars with `.env.example`
- Data directory permissions → `chown -R 1001:1001 /opt/meshwiki/data`
- Port conflict → `ss -tlnp | grep :8000`

### 503 Errors

```bash
# Check readiness probe
curl http://localhost:8000/health/ready

# Check container health status
docker inspect meshwiki --format='{{.State.Health.Status}}'

# Check volume mount
docker exec meshwiki ls /data/pages
```

If the volume is not mounted, restart the container:

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate meshwiki
```

### High Memory

```bash
docker stats meshwiki --no-stream
# If >256MB, restart to clear in-memory graph cache
docker compose -f docker-compose.prod.yml restart meshwiki
```

### Certificate Not Renewing

```bash
docker compose -f docker-compose.prod.yml logs caddy --tail=50
# Caddy auto-renews Let's Encrypt certs. If stuck:
docker compose -f docker-compose.prod.yml restart caddy
```

### Login Not Working

1. Check `MESHWIKI_AUTH_ENABLED=true` in `.env`
2. Check `MESHWIKI_AUTH_PASSWORD` is set
3. Check rate limit lockout: if 5+ failed attempts in 10 min, wait 10 minutes or restart the container to reset
4. Check session secret is set: `MESHWIKI_SESSION_SECRET` must be a long random string

---

## 4. Security Incidents

### Compromised Password

Invalidates all active sessions immediately:

```bash
ssh user@vps.yourdomain.com
cd /opt/meshwiki
# Edit .env: set new MESHWIKI_AUTH_PASSWORD and MESHWIKI_SESSION_SECRET
nano .env
docker compose -f docker-compose.prod.yml up -d --no-deps meshwiki
```

### Compromised API Key (Factory)

```bash
# Edit .env: rotate MESHWIKI_FACTORY_API_KEY
nano .env
docker compose -f docker-compose.prod.yml up -d --no-deps meshwiki
```

Revoke/rotate the key in the orchestrator config at the same time.

### Container CVE

```bash
# Scan the running image
docker pull ghcr.io/jyrkihuhta/meshwiki:latest
trivy image ghcr.io/jyrkihuhta/meshwiki:latest --severity CRITICAL,HIGH

# Fix: update base image in Dockerfile, push, let CI redeploy
# Or manually force redeploy after image is updated in GHCR:
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --no-deps meshwiki
```

---

## 5. Scaling

### Current Setup

Single container on a single VPS (Hetzner CX21: 2 vCPU, 4 GB RAM). Caddy handles TLS. Rust graph engine runs in-process.

### When to Upgrade

- p95 response time consistently above 500ms (`/metrics` → `meshwiki_http_request_duration_seconds`)
- Memory usage approaching 256MB (`docker stats`)
- Disk approaching 80% (`df -h /opt/meshwiki`)

### How to Scale (Vertical)

Upgrade the VPS size in Hetzner Cloud console. No application changes required. Brief downtime during resize (~2 minutes).

### Horizontal Scaling (Future)

Not yet supported — the graph engine holds state in-process and `data/pages/` is on a local volume. Horizontal scaling requires:
1. Shared storage (NFS or object storage) for `data/pages/`
2. Externalising graph engine state (Phase 3+)
3. A load balancer (Caddy can do round-robin `reverse_proxy`)
