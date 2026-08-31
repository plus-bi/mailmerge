# Deploying Local Mail Merge on Google Compute Engine

This guide deploys the application and worker as systemd user services on a Google Compute Engine VM. The dashboard remains private behind an IAP or SSH tunnel and uses Clerk for user authentication. Only the unsubscribe endpoint is publicly exposed through Caddy.

---

## 1. Architecture Overview

```mermaid
flowchart TD
    subgraph Users ["External Traffic"]
        Admin["Admin / Campaign Operator"]
        Recipient["Email Recipient"]
    end

    subgraph GCP ["Google Cloud Platform (VPC)"]
        subgraph GCE ["Compute Engine VM (Debian / Ubuntu)"]
            subgraph Public ["Public Ingress (Ports 80 / 443)"]
                Caddy["Caddy Reverse Proxy"]
                Unsub["Unsubscribe Service (Docker :8000)"]
            end

            subgraph Private ["Private / Localhost (Port 8765)"]
                IAP["Google Cloud IAP / SSH Tunnel"]
                API["MailMerge API & React UI"]
                Worker["MailMerge Worker (systemd)"]
                DB[("SQLite WAL DB & Attachments")]
                Keyring["Linux Secret Service (keyring)"]
            end
        end

        SMTPRelay["Outbound SMTP Relay (Port 587 / 465)<br/>Google Workspace / SendGrid / SES"]
    end

    Clerk["Clerk authentication"]

    Admin -->|IAP Tunnel (Port 8765)| IAP --> API
    Admin -->|Sign in| Clerk
    API -->|Verify JWT via JWKS| Clerk
    Recipient -->|HTTPS Unsubscribe Link| Caddy --> Unsub
    Unsub -->|Sync Unsubscribes| DB
    API --> DB
    API --> Keyring
    Worker --> DB
    Worker -->|Send Emails| SMTPRelay
```

---

## 2. Prerequisites

1. **GCP Project** with billing enabled and `gcloud` CLI installed & authenticated.
2. **Domain / DNS Record** (e.g. `unsub.yourdomain.com`) pointing to the VM's external IP for signed unsubscribe handling and automatic SSL certificate issuance.
3. **Authenticated SMTP Credentials** supporting Port 587 (STARTTLS) or Port 465 (SSL/TLS). *Note: GCP blocks outbound TCP port 25.*
4. **Clerk application** with a publishable key. The backend verifies Clerk JWTs through the application's JWKS endpoint; a legacy local session token is neither generated nor accepted.

---

## 3. Provisioning the GCE Virtual Machine

### 3.1. Create Firewall Rules

Allow public HTTP/HTTPS traffic (for the unsubscribe handler and Caddy TLS verification) while keeping the application port (8765) private:

```bash
# Allow HTTP/HTTPS for unsubscribe handling & automatic SSL
gcloud compute firewall-rules create allow-http-https \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=http-server,https-server

# Allow IAP TCP forwarding for secure admin tunneling
gcloud compute firewall-rules create allow-iap-tcp-forwarding \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=35.235.240.0/20
```

### 3.2. Launch the VM Instance

```bash
gcloud compute instances create mailmerge-vm \
    --zone=us-central1-a \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=20GB \
    --boot-disk-type=pd-balanced \
    --tags=http-server,https-server
```

> [!TIP]
> Reserve a static external IP in GCP Console (`VPC Network > External IP addresses`) and attach it to `mailmerge-vm` so your DNS records remain stable across restarts.

---

## 4. Server Environment Setup

SSH into the newly created instance:

```bash
gcloud compute ssh mailmerge-vm --zone=us-central1-a
```

### 4.1. Install System Dependencies

```bash
sudo apt-get update && sudo apt-get install -y \
    git \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    docker.io \
    docker-compose-v2 \
    dbus-x11 \
    gnome-keyring \
    pass

# Enable Docker for current user
sudo usermod -aG docker $USER
newgrp docker
```

---

## 5. Application Installation & Build

### 5.1. Clone Codebase & Install Python Package

```bash
sudo mkdir -p /opt/mailmerge
sudo chown $USER:$USER /opt/mailmerge
git clone <YOUR_GIT_REPO_URL> /opt/mailmerge
cd /opt/mailmerge

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 5.2. Configure the Runtime

Create `/opt/mailmerge/.env` and restrict it to the deployment user:

```bash
cp .env.example .env
chmod 600 .env
```

Set at least the following values:

```dotenv
MAILMERGE_HOST=127.0.0.1
MAILMERGE_PORT=8765
MAILMERGE_FRONTEND_ORIGIN=http://localhost:8765
MAILMERGE_DATA_DIR=/home/YOUR_USER/.local/share/mailmerge

CLERK_PUBLISHABLE_KEY=pk_live_replace_me
# Optional when it cannot be derived from the publishable key:
# CLERK_JWKS_URL=https://your-clerk-domain/.well-known/jwks.json

MAILMERGE_UNSUBSCRIBE_SYNC_URL=https://unsub.yourdomain.com/api/v1/events
```

Replace `YOUR_USER` with the Linux account that runs both services. Do not set `MAILMERGE_SESSION_TOKEN`; it is obsolete. Do not place the Clerk secret key in a frontend or `VITE_*` environment variable.

### 5.3. Configure Clerk

In the Clerk dashboard:

1. Add `http://localhost:8765` to the application's allowed origins and redirect URLs. This is the browser URL used through the tunnel.
2. Enable the sign-in methods required for operators.
3. Copy only the Clerk publishable key to the frontend configuration:

```bash
cat > /opt/mailmerge/frontend/.env.production.local <<'EOF'
VITE_CLERK_PUBLISHABLE_KEY=pk_live_replace_me
EOF
chmod 600 /opt/mailmerge/frontend/.env.production.local
```

The publishable key is intentionally embedded in the browser bundle. A Clerk secret key must never be added to this file.

### 5.4. Build & Deploy Frontend Assets

```bash
cd /opt/mailmerge/frontend
npm ci
npm run build

# Copy build artifacts to mailmerge static directory
mkdir -p ~/.local/share/mailmerge/frontend
cp -r dist/* ~/.local/share/mailmerge/frontend/
```

---

## 6. Configuring Background Services

### 6.1. MailMerge Web API (`systemd`)

Create a user unit for the web application:

```bash
mkdir -p ~/.config/systemd/user
cat <<EOF > ~/.config/systemd/user/mailmerge.service
[Unit]
Description=Local Mail Merge API and Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/mailmerge
EnvironmentFile=/opt/mailmerge/.env
ExecStart=/opt/mailmerge/.venv/bin/mailmerge
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
```

### 6.2. MailMerge Background Worker (`systemd`)

Create a user unit for the queue worker:

```bash
cat <<EOF > ~/.config/systemd/user/mailmerge-worker.service
[Unit]
Description=Local Mail Merge Background Worker
After=network.target mailmerge.service

[Service]
Type=simple
WorkingDirectory=/opt/mailmerge
EnvironmentFile=/opt/mailmerge/.env
ExecStart=/opt/mailmerge/.venv/bin/mailmerge-worker
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
```

### 6.3. Enable & Start Systemd Services

```bash
# Allow services to run without an active interactive login
sudo loginctl enable-linger $USER

systemctl --user daemon-reload
systemctl --user enable --now mailmerge.service
systemctl --user enable --now mailmerge-worker.service
```

---

## 7. Unsubscribe Microservice & Caddy Setup

Configure the public-facing signed unsubscribe service:

```bash
cd /opt/mailmerge/deploy

# Generate secure secrets
SIGNING_SECRET=$(openssl rand -hex 32)
SYNC_SECRET=$(openssl rand -hex 32)

cat <<EOF > .env
DOMAIN=unsub.yourdomain.com
UNSUBSCRIBE_SIGNING_SECRET=${SIGNING_SECRET}
UNSUBSCRIBE_SYNC_SECRET=${SYNC_SECRET}
EOF

# Give the worker the same synchronization secret.
printf '\nMAILMERGE_UNSUBSCRIBE_SYNC_SECRET=%s\n' "$SYNC_SECRET" >> /opt/mailmerge/.env

docker compose up -d
systemctl --user restart mailmerge.service mailmerge-worker.service
```

Create an `A` record for the configured unsubscribe hostname pointing to the VM's reserved external IP. Confirm that Caddy has obtained a certificate before using the unsubscribe URL in a campaign.

---

## 8. Secure Admin Access

Because the MailMerge API listens on `127.0.0.1:8765`, keep it unexposed to the public internet and connect securely via Google Cloud IAP Tunneling or SSH Port Forwarding.

### Option A: SSH Port Forward over IAP (Recommended)
From your local workstation:

```bash
gcloud compute ssh mailmerge-vm \
    --zone=us-central1-a \
    --tunnel-through-iap \
    -- -N -L 8765:127.0.0.1:8765
```

### Option B: SSH Port Forward
```bash
gcloud compute ssh mailmerge-vm --zone=us-central1-a -- -L 8765:127.0.0.1:8765
```

Open `http://localhost:8765` and sign in with Clerk. API requests without a valid Clerk session receive `401 Unauthorized`.

No `#token=...` URL fragment or local token file is used.

---

## 9. Verify the Deployment

On the VM, verify both systemd services and the unsubscribe containers:

```bash
systemctl --user --no-pager status mailmerge.service mailmerge-worker.service
docker compose --project-directory /opt/mailmerge/deploy ps
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/
```

From your workstation, open the IAP/SSH tunnel, sign in, then:

1. Create or import a sender profile and save it.
2. Confirm that `profiles.toml` exists under `MAILMERGE_DATA_DIR` unless `MAILMERGE_PROFILE_CONFIG` specifies another path.
3. Create a draft campaign and send a test message.
4. Check that an unauthenticated request such as `curl -i http://localhost:8765/api/v1/profiles` returns `401`.
5. Verify `https://unsub.yourdomain.com/` after DNS and certificate provisioning.

---

## 10. Deploying Updates

Use the shorter [existing-deployment update runbook](UPDATING.md). It includes the one-time Clerk migration, frontend asset deployment, service restart, and verification commands.

---

## 11. Backups & Disaster Recovery

### 11.1. GCP Persistent Disk Snapshots
Create a scheduled snapshot policy to automatically backup SQLite data and uploaded campaign attachments:

```bash
gcloud compute resource-policies create snapshot-schedule daily-mailmerge-backup \
    --region=us-central1 \
    --max-retention-days=14 \
    --daily-schedule \
    --start-time=03:00

gcloud compute disks add-resource-policies mailmerge-vm \
    --zone=us-central1-a \
    --resource-policies=daily-mailmerge-backup
```
