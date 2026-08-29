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

- 📖 [**User Guide** (`docs/USER_GUIDE.md`)](file:///home/haris/git/mailclient/docs/USER_GUIDE.md): Complete guide on writing templates, importing JSON data, live previewing, sending test emails, and configuring working hours.
- 🏗️ [**Architecture & Developer Reference** (`docs/ARCHITECTURE.md`)](file:///home/haris/git/mailclient/docs/ARCHITECTURE.md): System architecture, database schema, background worker lifecycle, and codebase extension points.
- 📡 [**REST API Reference** (`docs/API_REFERENCE.md`)](file:///home/haris/git/mailclient/docs/API_REFERENCE.md): Detailed documentation for all REST API endpoints, schemas, and SSE streams.
- ☁️ [**GCE VM Deployment Guide** (`deploy/GCE_DEPLOYMENT.md`)](file:///home/haris/git/mailclient/deploy/GCE_DEPLOYMENT.md): Production deployment plan on Google Cloud Platform using Compute Engine, systemd user services, IAP tunneling, and Caddy HTTPS proxy.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- Node.js 18+ & npm

### Installation & Setup

1. **Set up Python Virtual Environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   ```

2. **Build the React Frontend**:
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

3. **Start the API Server**:
   ```bash
   mailmerge
   ```

4. **Start the Background Worker**:
   ```bash
   mailmerge-worker
   ```

5. **Access the Web Dashboard**:
   - The API session token is generated at `~/.local/share/mailmerge/session-token`.
   - Open your browser to:
     ```
     http://127.0.0.1:8765/#token=<YOUR_SESSION_TOKEN>
     ```

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
