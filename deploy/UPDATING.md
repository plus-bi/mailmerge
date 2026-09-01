# Updating an Existing Deployment

This runbook assumes the application is already installed at `/opt/mailmerge` and runs as the current user's `mailmerge.service` and `mailmerge-worker.service` units.

## One-time Clerk migration

For the first deployment containing Clerk authentication, add the publishable key to the backend environment file:

```dotenv
# /opt/mailmerge/.env
CLERK_PUBLISHABLE_KEY=pk_live_replace_me
# Set this only when the JWKS URL cannot be derived from the publishable key:
# CLERK_JWKS_URL=https://your-clerk-domain/.well-known/jwks.json
```

Create the frontend build environment:

```bash
cd /opt/mailmerge/frontend
printf 'VITE_CLERK_PUBLISHABLE_KEY=pk_live_replace_me\n' > .env.production.local
chmod 600 .env.production.local
```

Only the publishable key belongs in the frontend file. Never put `CLERK_SECRET_KEY` or another secret in a `VITE_*` variable. Remove the obsolete `MAILMERGE_SESSION_TOKEN` setting if present. In Clerk, allow `http://localhost:8765` as an origin and redirect URL because that is the address used through the SSH tunnel.

## Deploy an update

The repository includes an update script. Run it as the deployment user, not with `sudo`:

```bash
cd /opt/mailmerge
./deploy/update.sh
```

That deploys the latest fast-forward commit from the current branch. To deploy a specific commit or remote ref:

```bash
./deploy/update.sh <commit-or-ref>
```

The script refuses a dirty checkout, installs backend dependencies, validates Python sources, creates the frontend Clerk environment from `/opt/mailmerge/.env`, installs and checks frontend dependencies, builds and copies the served assets, restarts both services, and verifies them. If deployment files changed, it also rebuilds the unsubscribe containers.

The service restart is non-blocking. The final requested status check is bounded to 15 seconds so a stuck user-systemd session cannot leave the deployment command hanging forever.

### Manual equivalent

Run on the VM as the same Linux user that owns the existing installation:

```bash
cd /opt/mailmerge
git status --short
git pull --ff-only

source .venv/bin/activate
pip install -e .

cd frontend
npm ci
npm run lint
npm run build
mkdir -p ~/.local/share/mailmerge/frontend
cp -a dist/. ~/.local/share/mailmerge/frontend/

systemctl --user daemon-reload
systemctl --user restart --no-block mailmerge.service mailmerge-worker.service
timeout 15s systemctl --user --no-pager status mailmerge.service mailmerge-worker.service
```

If `git status --short` reports local changes, stop and reconcile them before pulling so deployment-specific edits are not overwritten.

The copy step is required because the deployed service prefers the frontend build under `MAILMERGE_DATA_DIR` over `/opt/mailmerge/frontend/dist`.

If Compose configuration, Python dependencies, or unsubscribe-service source changed, rebuild the containerized services too:

```bash
cd /opt/mailmerge/deploy
docker compose up -d --build
docker compose ps
```

## Verify

From the VM:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://mailmerge.plus.bi/
curl -i https://mailmerge.plus.bi/api/v1/profiles
journalctl --user -u mailmerge.service -u mailmerge-worker.service -n 100 --no-pager
```

The UI request should succeed, while the unauthenticated API request should return `401`. Then open the tunnel from your workstation and sign in with Clerk:

```bash
gcloud compute ssh mailmerge-vm \
    --zone=us-central1-a \
    --tunnel-through-iap \
    -- -N -L 8765:127.0.0.1:8765
```

Open `http://localhost:8765`, confirm that campaigns and sender profiles load, and send a test email before launching a campaign.

If startup fails, inspect the journal output first. The existing SQLite database, attachments, profile TOML, and keyring credentials are retained during this update.
