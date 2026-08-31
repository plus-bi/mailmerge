# Local Mail Merge - User Guide

A privacy-focused, lightweight mail merge service for bulk email delivery. It allows you to send 100–200 personalized emails per day spread smoothly across working hours, validate all template variables beforehand, preview the exact message for any recipient, send sample test emails to yourself, and automatically process unsubscribe requests.

---

## Table of Contents

1. [Key Features & Capabilities](#key-features--capabilities)
2. [Quickstart & Accessing the UI](#quickstart--accessing-the-ui)
3. [End-to-End Workflow](#end-to-end-workflow)
   - [Step 1: Configure Sender Profile & Campaign Details](#step-1-configure-sender-profile--campaign-details)
   - [Step 2: Template Creation & Jinja2 Syntax](#step-2-template-creation--jinja2-syntax)
   - [Step 3: Ingesting Recipients via JSON](#step-3-ingesting-recipients-via-json)
   - [Step 4: Live Previews & Variable Inspection](#step-4-live-previews--variable-inspection)
   - [Step 5: Sending Test Emails](#step-5-sending-test-emails)
   - [Step 6: Daytime Delivery Pacing & Working Hours](#step-6-daytime-delivery-pacing--working-hours)
   - [Step 7: Preflight Check & Launch](#step-7-preflight-check--launch)
4. [Unsubscribe & Suppression List Management](#unsubscribe--suppression-list-management)
5. [Troubleshooting & FAQ](#troubleshooting--faq)

---

## Key Features & Capabilities

- **JSON-Only Batch Recipient Ingestion**: Direct support for raw JSON arrays or objects containing customized recipient metadata.
- **Strict Template Variable Validation**: Jinja2 AST automatically discovers all undeclared variables in your subject and body templates. Missing variables are flagged before sending to prevent embarrassing placeholder errors (e.g., `Hello {{ first_name }}`).
- **Live Per-Recipient Previews**: Interactive preview browser to inspect the rendered HTML and plain-text versions for any recipient alongside their raw JSON values.
- **Test Email Dispatch**: Send a live test email with prefix `[TEST]` to your inbox with a single click.
- **Daytime Delivery Pacing & Working Hours Guardrails**: Configure inter-message delays (e.g., 144 seconds) and restrict delivery exclusively to business hours (e.g., Mon–Fri, 09:00–17:00 in your preferred timezone). The background worker automatically pauses outside active hours.
- **Automated Unsubscribe Suppression**: Integrates with an RFC 8058-compliant unsubscribe microservice and syncs opt-outs into the suppression database.
- **Privacy & Security First**: Credentials are stored in the OS Secret Service / Keyring; no tracking pixels or URL rewrite cookies are injected.

---

## Quickstart & Accessing the UI

1. **Configure Clerk**:
   - Set `VITE_CLERK_PUBLISHABLE_KEY` when building the frontend.
   - Set `CLERK_PUBLISHABLE_KEY` or `CLERK_JWKS_URL` for the backend.
   - Add the application URL to Clerk's allowed origins and redirect URLs.
   - Never put a Clerk secret key in a `VITE_*` variable.
2. **Start the API server**:
   ```bash
   mailmerge
   ```
3. **Start the Background Worker** (in a separate terminal or systemd service):
   ```bash
   mailmerge-worker
   ```
4. **Open the Web UI** at `http://127.0.0.1:8765` and sign in through Clerk.

---

## End-to-End Workflow

### Step 1: Configure Sender Profile & Campaign Details

1. Click **"+ New Profile"** in the header to open the sender profile manager.
2. Create a profile by entering the SMTP server, authentication, and sending-limit settings, then click **Save profile**. The credential is stored in the OS keychain; the remaining settings are saved to the configured TOML file or the app-managed `profiles.toml` in its data directory.
3. Use **Load TOML** to import and activate an existing `[[profiles]]` file. Use **Download TOML** to save a credential-free backup.
4. Select an existing campaign or click **"+ New Campaign"**.
5. Under the **📝 Setup & Template** tab:
   - **Campaign Name**: e.g. `Q3 Community Update`.
   - **Sender Profile**: Select your configured SMTP profile (e.g. `LRZ`, `Postmark`, `Gmail/Workspace`).
   - **From Name**: The display name visible in the recipient's email client (e.g., `Alice from Acme`).
   - **From Address**: The sender email (e.g., `alice@yourdomain.com`).
   - **Reply-To Address**: Set a dedicated reply address if you want responses routed to a different inbox (e.g., `replies@yourdomain.com`).
   - **Body Mode**: Choose between `Markdown` (recommended) or raw `HTML`.

---

### Step 2: Template Creation & Jinja2 Syntax

Write your **Subject Template** and **Body Template** using Jinja2 syntax:

#### Example Subject:
```jinja2
{{ company }} update: Invitation for {{ first_name }}
```

#### Example Markdown Body:
```markdown
Hi {{ first_name }},

I noticed your recent work at **{{ company }}** regarding {{ project_name }}.

We are hosting an exclusive session for {{ role | title }} leaders next Tuesday.

Best regards,  
Jane Doe  
Acme Corp
```

#### Available Template Filters:
- `{{ value | lower }}`: Converts to lowercase.
- `{{ value | upper }}`: Converts to uppercase.
- `{{ value | title }}`: Capitalizes each word.
- `{{ value | trim }}`: Trims leading and trailing whitespace.
- `{{ value | default('there') }}`: Provides a fallback if undefined.

---

### Step 3: Ingesting Recipients via JSON

Under the **👥 Recipients** tab, paste your JSON array into the batch ingestion box.

#### Accepted Formats:

**Format A: Flat Object List (Recommended)**
```json
[
  {
    "email": "alex@example.com",
    "first_name": "Alex",
    "company": "Acme Corp",
    "project_name": "Cloud Migration",
    "role": "cto"
  },
  {
    "email": "sarah@example.com",
    "first_name": "Sarah",
    "company": "Globex Inc",
    "project_name": "DevSecOps",
    "role": "vp engineering"
  }
]
```

**Format B: Nested `values` Structure**
```json
[
  {
    "email": "jordan@example.com",
    "values": {
      "first_name": "Jordan",
      "company": "Soylent",
      "project_name": "Supply Chain",
      "role": "lead architect"
    }
  }
]
```

#### Validation & Deduplication:
- Emails are normalized and deduplicated case-insensitively.
- The system immediately verifies that all variables used in your templates exist in each recipient's payload.
- Any recipient with missing variables is tagged as `Invalid` with an explicit notice (e.g. `missing required template variable(s): project_name`).

---

### Step 4: Live Previews & Variable Inspection

Switch to the **👁 Live Previews** tab:
1. Choose any recipient from the dropdown selector.
2. The UI renders the exact **Subject**, formatted **HTML output**, and **Plain Text fallback**.
3. View the recipient's raw JSON key-value pairs side-by-side to verify accuracy before sending.

---

### Step 5: Sending Test Emails

Before sending to your full recipient list, send a live preview to your personal inbox:
1. Go to the **🚀 Preflight & Send** tab.
2. Under **✉️ Send Test Email**, enter your email address (e.g. `you@domain.com`).
3. Click **Send Test Email**.
4. The system renders the sample template, prepends `[TEST]` to the subject, and sends it directly via the configured SMTP server.

---

### Step 6: Daytime Delivery Pacing & Working Hours

In the **📝 Setup & Template** tab:

1. **Delay Between Emails**:
   - To spread **200 emails across an 8-hour workday**:
     $$\text{Delay} = \frac{8 \times 3600 \text{ seconds}}{200} = 144 \text{ seconds}$$
   - Set **Delay Between Emails** to `144`.
2. **Working Hours Restriction**:
   - Check **"Restricted to Working Hours"**.
   - **Start Hour**: `9` (09:00 local time).
   - **End Hour**: `17` (17:00 local time).
   - **Timezone**: e.g., `Europe/Berlin`, `America/New_York`, `UTC`.
3. **Behavior**:
   - Dispatch runs only Monday through Friday between start and end hours.
   - If the clock passes the end hour or enters the weekend, the worker pauses delivery and automatically resumes at 09:00 on the next business day.

---

### Step 7: Preflight Check & Launch

1. Under the **🚀 Preflight & Send** tab, click **Run Full Preflight Check**.
2. The preflight check confirms:
   - ✅ Sender profile connectivity and authentication.
   - ✅ All template variables are fully populated across all recipients.
   - ✅ Total message sizes comply with profile limits.
   - ✅ Daily sending caps are not exceeded.
   - ✅ Marketing consent and suppression list requirements are met (for marketing campaigns).
3. Once passed, click **🚀 Launch Campaign**.
4. Monitor live delivery counts (Sent, Pending, Failed, Retry) and real-time progress bars via Server-Sent Events (SSE).

---

## Unsubscribe & Suppression List Management

For marketing and outreach campaigns:
1. **One-Click Unsubscribe**: Headers compliant with RFC 8058 (`List-Unsubscribe` and `List-Unsubscribe-Post`) are automatically generated.
2. **Signed Unsubscribe URLs**: Each recipient receives a cryptographically signed HMAC token URL.
3. **Suppression Sync**:
   - The worker periodically polls the unsubscribe service and marks unsubscribed recipients as `suppressed = True`.
   - You can manually trigger a sync at any time via the **"Sync Unsubscribe List Now"** button in the UI.

---

## Troubleshooting & FAQ

### 1. "Preflight failed: missing required template variable(s)"
- **Cause**: Your subject or body template contains a placeholder (e.g., `{{ title }}`) that is missing from one or more recipient records in your JSON data.
- **Fix**: Update the recipient's JSON data or adjust your template to provide a default fallback: `{{ title | default('Team Member') }}`.

### 2. "Rolling daily cap exceeded"
- **Cause**: The number of scheduled emails plus emails sent in the last 24 hours exceeds the profile's `daily_cap`.
- **Fix**: Either schedule the remaining emails for tomorrow or approve the guardrail override prompt.

### 3. "Authentication failed"
- **Cause**: SMTP credentials stored in keyring or environment were rejected by the remote mail server.
- **Fix**: Check `mailmerge.secrets` or recreate the profile with the correct app password / API token.
