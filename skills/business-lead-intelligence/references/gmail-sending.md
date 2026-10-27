# Gmail sending

The pipeline writes drafts. Sending is a separate, explicit step. `scripts/gmail_sender.py` sends only rows marked `cold_email_status=ready` with a published, valid email. It never runs from `run_pipeline.py`.

The official Gmail MCP server is not required. It is currently gated behind the Google Workspace Developer Preview Program and exposes draft tools, not a send tool. This sender uses the Gmail API directly with the `gmail.compose` scope, which allows sending.

## What the sender enforces

- Only `cold_email_status=ready` rows.
- Only a single, published, syntactically valid email per row.
- No guessed or personal addresses.
- Sender placeholders must be replaced before `--send`.
- Duplicate suppression through a local sent log.
- A delay between sends, default 3 seconds.
- Dry-run by default. Actual sending requires both `--send` and `--yes`.
- Plain-text email with a natural opt-out line and the postal address.
- No HTML, bold, images, tracking pixels, or links in the first touch.
- Optional `List-Unsubscribe` header.

The sender does not warm domains, verify inboxes, manage replies, or decide whether outreach is legally appropriate. Use the `cold-email` skill for deliverability and cadence, and follow local outreach law.

## Prerequisites

1. A Google Cloud project with the **Gmail API** enabled.
2. An OAuth client of type **Web application** or **Desktop app**.
3. This authorized redirect URI for a Web client:

   ```text
   http://127.0.0.1:19876/mcp/oauth/callback
   ```

4. The `https://www.googleapis.com/auth/gmail.compose` scope added to the OAuth consent screen. If the app is in testing mode, add the sending Google account as a test user.

Put the downloaded client JSON in one of these places:

```text
.opencode/secrets/gmail_client.json
client_secret_*.json
.opencode/client_secret_*.json
```

Then restrict it:

```bash
chmod 600 .opencode/secrets/gmail_client.json
```

You can also pass `--client-secret /path/to/client_secret_*.json`.

## Authorize once

```bash
.venv/bin/python "$SKILL_DIR/scripts/gmail_sender.py" --auth
```

The command opens a browser. Approve the Gmail scope. The refresh token is written to `.opencode/secrets/gmail_token.json` with mode `600`.

If the browser does not open:

```bash
.venv/bin/python "$SKILL_DIR/scripts/gmail_sender.py" --auth --no-browser
```

Open the printed URL manually. The callback listener uses port `19876`; close any other OAuth flow using that port.

Verify the account:

```bash
.venv/bin/python "$SKILL_DIR/scripts/gmail_sender.py" --whoami
```

## Dry run

Always review first:

```bash
.venv/bin/python "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/lahore_dentists_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --show-body
```

The dry run prints each recipient and subject, and warns about remaining placeholders. It does not contact Gmail.

Filter by business name or recipient:

```bash
--only "Dental Avenue,OraDent"
--only "info@example.com"
```

Limit the batch:

```bash
--limit 5
```

`--limit 0` means all ready rows.

## Send

After reviewing the dry run:

```bash
.venv/bin/python "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/lahore_dentists_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --send --yes --limit 5 --delay 5
```

Without `--yes`, an interactive terminal asks you to type `SEND`. In a non-interactive agent run, `--yes` is required.

Optional headers:

```bash
--from "Your Name <you@yourdomain.com>" \
--unsubscribe "you@yourdomain.com"
```

If `--from` is omitted, Gmail uses the authenticated account. The `From` address must be an alias or send-as address allowed by that Gmail account.

## Sent log and duplicates

Successful sends are recorded in `.opencode/secrets/gmail_sent.json`:

```json
{
  "info@example.com": {
    "sent_at": "2026-10-27T14:00:00Z",
    "message_id": "18...",
    "business": "Example Clinic",
    "subject": "clinic security"
  }
}
```

The log prevents accidental duplicate sends. Use `--resend` only when you intentionally need to send again. Delete the log entry if a send failed before Gmail accepted it.

## Scopes

The default scope is `gmail.compose`, which covers sending. To request the narrower send-only scope instead:

```bash
--scopes "https://www.googleapis.com/auth/gmail.send"
```

The scope must already be configured on the OAuth consent screen. Re-run `--auth` after changing scopes.

## Troubleshooting

**`redirect_uri_mismatch`**
Add `http://127.0.0.1:19876/mcp/oauth/callback` to the OAuth client's authorized redirect URIs.

**`invalid_scope`**
Add `gmail.compose` (or `gmail.send`) to the OAuth consent screen, then re-run `--auth`.

**`access_denied`**
If the app is in testing mode, add the Google account as a test user.

**`No usable Gmail token`**
Run `--auth`.

**`Token refresh failed`**
The refresh token was revoked, expired, or the client secret changed. Run `--auth` again.

**`Gmail send failed: 403`**
Confirm the Gmail API is enabled, the token has the send/compose scope, and the account has not hit a Gmail sending limit.

**`Could not listen on 127.0.0.1:19876`**
Another OAuth flow is using the port. Close it and retry.

**No refresh token returned**
The flow requests `access_type=offline` and `prompt=consent`. If Google still omits it, remove the token file and re-run `--auth`.

## Revoke access

Remove the local token:

```bash
rm .opencode/secrets/gmail_token.json
```

Revoke the app in your Google Account permissions page:

<https://myaccount.google.com/permissions>

## Compliance

Cold email rules vary by country. Before sending:

- use a real sender identity and postal address;
- keep the plain-text opt-out;
- stop contacting anyone who opts out;
- send only to published business addresses;
- do not claim results, rankings, or revenue you cannot prove;
- respect Gmail's sending limits and your provider's terms.
