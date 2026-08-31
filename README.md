# Local Mail Merge

A privacy-first, lightweight bulk email delivery engine designed for sending personalized mail merges (100–200 emails) spread smoothly over working hours with complete template variable validation, live message previewing, test email dispatch, and automated unsubscribe processing.

---

## ✨ Features at a Glance

- 📄 **JSON Batch Ingestion**: Ingest recipient lists with personalized key-value payloads directly via JSON (flat arrays or nested objects).
- 🔍 **Strict Template Variable Validation**: Jinja2 AST analyzes your subject and body templates to guarantee that all required variables are populated for every recipient before sending.
- 👁️ **Live Per-Recipient Previews**: Interactive previewer rendering the exact Subject, HTML, and Plain-text message side-by-side with raw JSON values.
- ✉️ **Send Test Email to Me**: Dispatch instant sample test emails to verify layout and headers in your actual inbox before campaign launch.
- ⏱️ **Daytime Pacing & Working Hours Guardrails**: Configure inter-message delays (e.g. 144 seconds) and restrict delivery exclusively to business hours (e.g. 09:00–17:00 weekdays in your local timezone).
- 🛡️ **Automated Unsubscribe Suppression**: Automatic synchronization of RFC 8058 one-click and HMAC-signed unsubscribe requests to the suppression database.
- 🔒 **Zero Tracking & Secure Credentials**: No tracking pixels, open beacons, or URL rewrites. Passwords and tokens are stored in the OS Secret Service / Keyring.

---

## 📚 Documentation

- 📖 [**User Guide** (`docs/USER_GUIDE.md`)](docs/USER_GUIDE.md): Templates, recipient import, sender profiles, previews, test messages, and delivery controls.
- 🏗️ [**Architecture & Developer Reference** (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md): Components, data model, authentication, and worker lifecycle.
- 📡 [**REST API Reference** (`docs/API_REFERENCE.md`)](docs/API_REFERENCE.md): API endpoints, schemas, and authentication.
- ☁️ [**GCE Deployment Guide** (`deploy/GCE_DEPLOYMENT.md`)](deploy/GCE_DEPLOYMENT.md): VM provisioning, Clerk configuration, systemd services, unsubscribe hosting, verification, updates, and backups.
- 🔄 [**Deployment Update Runbook** (`deploy/UPDATING.md`)](deploy/UPDATING.md): Short checklist for updating an existing installation.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- Node.js 18+ & npm
- A Clerk application

### Installation & Setup

1. **Set up Python Virtual Environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   ```

2. **Configure Clerk and build the React frontend**:
   ```bash
   cd frontend
   printf 'VITE_CLERK_PUBLISHABLE_KEY=pk_test_replace_me\n' > .env.local
   npm ci
   npm run build
   cd ..
   ```

   Only put the publishable key in a `VITE_*` variable. Never put a Clerk secret key in frontend environment files.

3. **Start the API Server**:
   ```bash
   mailmerge
   ```

4. **Start the Background Worker**:
   ```bash
   mailmerge-worker
   ```

5. **Access the Web Dashboard**:
   Open `http://127.0.0.1:8765` and sign in through Clerk. Add this URL to the allowed origins and redirect URLs for your Clerk application.

For a GCE production deployment, follow [deploy/GCE_DEPLOYMENT.md](deploy/GCE_DEPLOYMENT.md).

---

## 🧪 Running Tests

```bash
# Run pytest test suite
.venv/bin/pytest

# Run tests with verbose output
.venv/bin/pytest -vv
```

---

## 🛡️ License

MIT License.
