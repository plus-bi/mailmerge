# Local Mail Merge

A Linux-first local mail-merge application. The FastAPI API binds to loopback, stores campaign state in SQLite/WAL, keeps credentials in Secret Service through `keyring`, and sends one message per recipient. A separate Docker/Caddy service handles signed unsubscribe links.

## Development

Requires Python 3.12 and Node.js.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
mailmerge
```

The API session token is created at `~/.local/share/mailmerge/session-token`. Build the UI with `cd frontend && npm install && npm run build`, then copy `frontend/dist` to the configured data directory as `frontend`.

Run the worker separately with `mailmerge-worker`. Production packaging installs `packaging/mailmerge-worker.service` as a user unit.

## Unsubscribe deployment

Set `DOMAIN`, `UNSUBSCRIBE_SIGNING_SECRET`, and `UNSUBSCRIBE_SYNC_SECRET` in `deploy/.env`, point DNS at the host, and run `docker compose up -d` from `deploy/`. Ports 80 and 443 must be reachable so Caddy can provision HTTPS.

No open pixels or click tracking are implemented.

