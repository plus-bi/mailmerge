# Local Mail Merge - REST API Reference

Base URL: `http://127.0.0.1:8765/api/v1`  
Authentication: Clerk session JWT in `Authorization: Bearer <clerk-jwt>`, or the same-origin Clerk `__session` cookie. Legacy local session tokens are not accepted.

---

## 1. Profiles (`/profiles`)

### `GET /profiles`
Returns all configured SMTP sender profiles.

#### Response `200 OK`:
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "LRZ Postout",
    "smtp_host": "postout.lrz.de",
    "smtp_port": 587,
    "security": "starttls",
    "verify_tls": true,
    "username": "tum_user",
    "auth_type": "password",
    "daily_cap": 250,
    "delay_seconds": 2,
    "max_message_bytes": 20000000,
    "reply_to": "replies@example.tum.de",
    "list_unsubscribe": null,
    "list_unsubscribe_one_click": false,
    "working_hours_enabled": false,
    "working_hours_start": 9,
    "working_hours_end": 17,
    "working_hours_timezone": "UTC",
    "imap_host": null,
    "imap_port": null,
    "imap_security": null
  }
]
```

### `POST /profiles`
Creates a new sender profile.

#### Request Body:
```json
{
  "name": "Company SMTP",
  "smtp_host": "smtp.company.com",
  "smtp_port": 587,
  "security": "starttls",
  "verify_tls": true,
  "username": "sender@company.com",
  "password": "secret-password",
  "daily_cap": 250,
  "delay_seconds": 2,
  "max_message_bytes": 20000000,
  "reply_to": "support@company.com",
  "working_hours_enabled": true,
  "working_hours_start": 9,
  "working_hours_end": 17,
  "working_hours_timezone": "Europe/Berlin"
}
```

### `PUT /profiles/{profile_id}`
Updates an existing sender profile. Supplying a blank credential preserves the credential already stored in the OS keychain.

### `GET /profile-config`
Returns the active TOML profile file for download. Credentials are never included in generated TOML.

### `POST /profile-config`
Validates and imports TOML supplied as `{ "content": "..." }`, upserts profiles by name, and stores the file as the active configuration.

### `PUT /profile-config`
Serializes all current database profiles and atomically saves them to the active TOML configuration file.

---

## 2. Campaigns (`/campaigns`)

### `GET /campaigns`
Lists all campaigns sorted by creation date descending.

### `POST /campaigns`
Creates a new draft campaign.

#### Request Body:
```json
{
  "name": "Q3 Launch Outreach",
  "purpose": "operational",
  "profile_id": "550e8400-e29b-41d4-a716-446655440000",
  "from_name": "Alice Smith",
  "from_address": "alice@company.com",
  "reply_to": "alice-replies@company.com",
  "subject_template": "Hello {{ first_name }} - {{ company }} Project",
  "body_mode": "markdown",
  "body_template": "Hi {{ first_name }},\n\nHope all is well at **{{ company }}**.\n\nBest,\nAlice",
  "delay_seconds": 144,
  "working_hours_enabled": true,
  "working_hours_start": 9,
  "working_hours_end": 17,
  "working_hours_timezone": "Europe/Berlin",
  "consent_acknowledged": false,
  "unsubscribe_base_url": null
}
```

### `GET /campaigns/{campaign_id}`
Fetches campaign metadata by ID.

### `PUT /campaigns/{campaign_id}`
Updates an existing draft or paused campaign.

---

## 3. Recipients & JSON Batch Ingestion (`/campaigns/{id}/recipients`)

### `GET /campaigns/{campaign_id}/recipients`
Returns all recipient records for the campaign.

#### Response `200 OK`:
```json
[
  {
    "id": "rec-1",
    "campaign_id": "camp-1",
    "email": "alex@example.com",
    "normalized_email": "alex@example.com",
    "values": {
      "first_name": "Alex",
      "company": "Acme Corp"
    },
    "included": true,
    "valid": true,
    "validation_error": null,
    "suppressed": false,
    "status": "pending",
    "message_id": null,
    "sent_at": null
  }
]
```

### `POST /campaigns/{campaign_id}/recipients`
Imports recipients from a JSON array or object. Automatically extracts template variables from the campaign's subject and body templates and flags any recipient missing required variables.

#### Request Body (JSON Array):
```json
[
  {
    "email": "alex@example.com",
    "first_name": "Alex",
    "company": "Acme Corp"
  },
  {
    "email": "bob@example.com",
    "values": {
      "first_name": "Bob",
      "company": "Globex Inc"
    }
  }
]
```

#### Response `200 OK`:
```json
{
  "keys": ["company", "email", "first_name"],
  "imported": 2,
  "valid": 2,
  "duplicates": 0,
  "errors": []
}
```

---

## 4. Previews & Live Rendering

### `GET /campaigns/{campaign_id}/preview/{recipient_id}`
Renders the exact email message for a specific recipient.

#### Response `200 OK`:
```json
{
  "recipient_id": "rec-1",
  "email": "alex@example.com",
  "subject": "Hello Alex - Acme Corp Project",
  "html": "<p>Hi Alex,</p><p>Hope all is well at <strong>Acme Corp</strong>.</p><p>Best,<br>Alice</p>",
  "text": "Hi Alex,\n\nHope all is well at Acme Corp.\n\nBest,\nAlice",
  "values": {
    "first_name": "Alex",
    "company": "Acme Corp"
  },
  "missing_variables": []
}
```

---

## 5. Preflight & Verification

### `POST /campaigns/{campaign_id}/preflight`
Runs preflight validation across all recipients and verifies required template variables.

#### Response `200 OK`:
```json
{
  "ok": true,
  "errors": [],
  "previews": [
    {
      "recipient_id": "rec-1",
      "email": "alex@example.com",
      "subject": "Hello Alex - Acme Corp Project",
      "html": "...",
      "text": "...",
      "size": 1420,
      "values": { "first_name": "Alex", "company": "Acme Corp" },
      "missing_variables": []
    }
  ],
  "excluded": 0,
  "attachments": []
}
```

---

## 6. Test Email Dispatch

### `POST /campaigns/{campaign_id}/test-email`
Renders a sample message and dispatches a test email immediately to the specified address.

#### Request Body:
```json
{
  "recipient_email": "tester@domain.com",
  "sample_recipient_id": null
}
```

#### Response `200 OK`:
```json
{
  "ok": true,
  "recipient_email": "tester@domain.com",
  "subject": "[TEST] Hello Alex - Acme Corp Project"
}
```

---

## 7. Unsubscribe Suppression Sync

### `GET /suppressions`
Lists unsubscribe events already synchronized into the Mailmerge database, newest first. The response includes `email`, `campaign`, and `unsubscribed_at` for each event, plus the most recent manual synchronization time in `last_synced_at`.

### `POST /suppressions/sync`
Manually fetches new unsubscribe events from the configured unsubscribe service, stores their metadata, and marks matching recipients as suppressed across campaigns.

#### Response `200 OK`:
```json
{
  "ok": true,
  "synced_events": 3,
  "total": 7
}
```

---

## 8. Scheduling & Lifecycle Control

### `POST /campaigns/{campaign_id}/schedule`
Schedules the campaign for background delivery.

#### Request Body:
```json
{
  "scheduled_at": "2026-08-29T14:00:00Z",
  "confirm_guardrail_override": false
}
```

### `POST /campaigns/{campaign_id}/{action}`
Performs a lifecycle transition action (`pause`, `resume`, `cancel`, `confirm-overdue`).

### `GET /campaigns/{campaign_id}/events`
Server-Sent Events (SSE) stream streaming real-time status and delivery counts (`sent`, `pending`, `retry`, `failed`).
