#!/usr/bin/env python3
"""Send reviewed cold-email drafts through the Gmail API.

This is the explicit sending step. It never runs automatically from the
pipeline. It reads the CSV produced by build_report.py, filters rows marked
``cold_email_status=ready``, replaces sender placeholders, and sends only when
``--send --yes`` is present.

Examples:
    # One-time OAuth consent (opens a browser)
    python gmail_sender.py --auth

    # Verify the authenticated Gmail account
    python gmail_sender.py --whoami

    # Preview what would be sent
    python gmail_sender.py \
        --csv output/lahore_dentists_all_leads.csv \
        --sender-name "Your Name" \
        --sender-address "Your postal address"

    # Send the first 5, after reviewing the dry run
    python gmail_sender.py \
        --csv output/lahore_dentists_all_leads.csv \
        --sender-name "Your Name" \
        --sender-address "Your postal address" \
        --send --yes --limit 5

The script uses only requests plus the Python standard library. It stores the
refresh token and sent log under .opencode/secrets/ with 600 permissions.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URI = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_PROFILE_URI = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
DEFAULT_REDIRECT_URI = "http://127.0.0.1:19876/mcp/oauth/callback"
DEFAULT_TOKEN_PATH = Path(".opencode/secrets/gmail_token.json")
DEFAULT_LOG_PATH = Path(".opencode/secrets/gmail_sent.json")
PLACEHOLDER_RE = re.compile(r"\[Your[^\]]*\]", re.IGNORECASE)


class GmailSenderError(RuntimeError):
    """A user-facing sender error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_client_file(explicit: Path | None = None) -> Path:
    if explicit:
        if not explicit.is_file():
            raise GmailSenderError(f"OAuth client file not found: {explicit}")
        return explicit
    candidates = [
        Path(".opencode/secrets/gmail_client.json"),
        *sorted(Path(".").glob("client_secret_*.json")),
        *sorted(Path(".opencode").glob("client_secret_*.json")),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise GmailSenderError(
        "No OAuth client JSON found. Pass --client-secret /path/to/client_secret_*.json."
    )


def load_client(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GmailSenderError(f"Could not read OAuth client file: {exc}") from exc
    block = data.get("web") or data.get("installed") or data
    if not isinstance(block, dict):
        raise GmailSenderError("OAuth client file must contain a web or installed object.")
    client_id = str(block.get("client_id") or "").strip()
    client_secret = str(block.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise GmailSenderError("OAuth client file is missing client_id or client_secret.")
    redirects = [str(uri) for uri in block.get("redirect_uris") or []]
    redirect_uri = DEFAULT_REDIRECT_URI
    for candidate in redirects:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
            redirect_uri = candidate
            break
    if redirects and redirect_uri not in redirects:
        raise GmailSenderError(
            "The OAuth client has no loopback redirect URI. Add "
            f"{DEFAULT_REDIRECT_URI} to its authorized redirect URIs."
        )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def load_json_file(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GmailSenderError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GmailSenderError(f"{path} must contain a JSON object.")
    return data


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def token_is_fresh(token: dict) -> bool:
    try:
        expires_at = float(token.get("expires_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(token.get("access_token")) and expires_at > time.time() + 60


def refresh_access_token(client: dict, token: dict) -> dict:
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise GmailSenderError("No refresh token. Run --auth again.")
    response = requests.post(
        TOKEN_URI,
        data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise GmailSenderError(
            "Token refresh failed. Run --auth again. "
            f"Google said: {response.text[:300]}"
        )
    payload = response.json()
    token = dict(token)
    token["access_token"] = payload.get("access_token", "")
    token["expires_in"] = int(payload.get("expires_in") or 0)
    token["expires_at"] = int(time.time()) + token["expires_in"]
    token["scope"] = payload.get("scope", token.get("scope", ""))
    if payload.get("refresh_token"):
        token["refresh_token"] = payload["refresh_token"]
    token["refreshed_at"] = utc_now()
    return token


class _CallbackServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], path: str):
        super().__init__(address, handler)
        self.callback_path = path
        self.result: dict | None = None
        self.done = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        parsed = urllib.parse.urlparse(self.path)
        server = self.server
        if parsed.path != server.callback_path:
            self.send_error(404)
            return
        params = urllib.parse.parse_qs(parsed.query)
        server.result = {key: values[0] for key, values in params.items() if values}
        server.done.set()
        body = (
            "<html><body style='font-family:sans-serif;max-width:36rem;margin:4rem auto'>"
            "<h2>Gmail authorization received</h2>"
            "<p>You can close this tab and return to the terminal.</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib API
        return


def run_oauth_flow(
    client: dict,
    *,
    token_path: Path,
    scopes: list[str],
    open_browser: bool,
    timeout: int,
) -> dict:
    redirect_uri = client["redirect_uri"]
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise GmailSenderError("The redirect URI must be a local http loopback URI.")
    port = parsed.port or 80
    path = parsed.path or "/"

    state = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
    )
    auth_url = f"{AUTH_URI}?{query}"

    try:
        server = _CallbackServer((parsed.hostname, port), _CallbackHandler, path)
    except OSError as exc:
        raise GmailSenderError(
            f"Could not listen on {parsed.hostname}:{port}. Close any other OAuth flow and retry. {exc}"
        ) from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("Open this URL and approve the Gmail scope:")
    print("")
    print(auth_url)
    print("")
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:  # noqa: BLE001 - browser opening is best effort
            pass
    print(f"Waiting for the callback on {redirect_uri} ...")
    try:
        if not server.done.wait(timeout=timeout):
            raise GmailSenderError("Timed out waiting for Google authorization.")
    finally:
        server.shutdown()
        server.server_close()

    result = server.result or {}
    if result.get("error"):
        raise GmailSenderError(f"Google returned: {result['error']}")
    if result.get("state") != state:
        raise GmailSenderError("OAuth state mismatch. Try --auth again.")
    code = result.get("code")
    if not code:
        raise GmailSenderError("Google did not return an authorization code.")

    response = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise GmailSenderError(
            "Token exchange failed. "
            f"Google said: {response.text[:300]}"
        )
    payload = response.json()
    token = {
        "access_token": payload.get("access_token", ""),
        "refresh_token": payload.get("refresh_token", ""),
        "expires_in": int(payload.get("expires_in") or 0),
        "expires_at": int(time.time()) + int(payload.get("expires_in") or 0),
        "scope": payload.get("scope", " ".join(scopes)),
        "token_type": payload.get("token_type", "Bearer"),
        "created_at": utc_now(),
    }
    if not token["access_token"]:
        raise GmailSenderError("Google did not return an access token.")
    write_private_json(token_path, token)
    print(f"[ok] Token saved to {token_path} (mode 600).")
    if not token["refresh_token"]:
        print("[warn] Google did not return a refresh token. Re-run --auth if it expires.")
    return token


def get_access_token(
    client: dict,
    token_path: Path,
    *,
    scopes: list[str],
    auth: bool,
    open_browser: bool,
    timeout: int,
) -> str:
    token = load_json_file(token_path)
    if token_is_fresh(token):
        return str(token["access_token"])
    if token.get("refresh_token"):
        token = refresh_access_token(client, token)
        write_private_json(token_path, token)
        return str(token["access_token"])
    if auth:
        token = run_oauth_flow(
            client,
            token_path=token_path,
            scopes=scopes,
            open_browser=open_browser,
            timeout=timeout,
        )
        return str(token["access_token"])
    raise GmailSenderError(
        f"No usable Gmail token at {token_path}. Run --auth first."
    )


def gmail_profile(access_token: str) -> dict:
    response = requests.get(
        GMAIL_PROFILE_URI,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if response.status_code != 200:
        raise GmailSenderError(
            "Could not read the Gmail profile. "
            f"Google said: {response.text[:300]}"
        )
    return response.json()


def build_raw_message(
    *,
    to: str,
    subject: str,
    body: str,
    sender: str = "",
    unsubscribe: str = "",
) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    sender_address = parseaddr(sender)[1] if sender else ""
    domain = sender_address.split("@", 1)[1] if "@" in sender_address else "localhost"
    message["Message-ID"] = make_msgid(domain=domain)
    if sender:
        message["From"] = sender
    if unsubscribe:
        message["List-Unsubscribe"] = f"<mailto:{unsubscribe}?subject=unsubscribe>"
    message.set_content(body, subtype="plain", charset="utf-8")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def send_raw_message(access_token: str, raw: str) -> dict:
    response = requests.post(
        GMAIL_SEND_URI,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"raw": raw},
        timeout=30,
    )
    if response.status_code != 200:
        raise GmailSenderError(
            "Gmail send failed. "
            f"Google said: {response.text[:500]}"
        )
    return response.json()


def validate_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", value.strip()))


def apply_sender_placeholders(
    body: str,
    *,
    sender_name: str,
    sender_company: str,
    sender_address: str,
) -> str:
    replacements = {
        "[Your name]": sender_name,
        "[Your company]": sender_company,
        "[Your postal address]": sender_address,
    }
    for placeholder, value in replacements.items():
        if value:
            body = re.sub(re.escape(placeholder), value, body, flags=re.IGNORECASE)
    if not sender_company:
        body = re.sub(r"(?im)^[ \t]*\[Your company\][ \t]*\n?", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    return body


def load_ready_rows(
    csv_path: Path,
    *,
    only: str = "",
    limit: int = 10,
) -> list[dict]:
    if not csv_path.is_file():
        raise GmailSenderError(f"CSV not found: {csv_path}")
    needles = [item.strip().lower() for item in only.split(",") if item.strip()]
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("cold_email_status") or "").strip().lower() != "ready":
                continue
            email = (row.get("email") or "").strip()
            subject = (row.get("cold_email_subject") or "").strip()
            body = (row.get("cold_email_body") or "").strip()
            business = (row.get("business_name") or row.get("name") or "").strip()
            if not email or not subject or not body:
                continue
            if not validate_email(email):
                continue
            haystack = f"{business} {email}".lower()
            if needles and not any(needle in haystack for needle in needles):
                continue
            rows.append(
                {
                    "business": business,
                    "email": email,
                    "subject": subject,
                    "body": body,
                }
            )
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def load_sent_log(path: Path) -> dict:
    data = load_json_file(path, {})
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send reviewed cold-email drafts through the Gmail API."
    )
    parser.add_argument("--csv", type=Path, help="CSV produced by build_report.py.")
    parser.add_argument("--client-secret", type=Path, help="Google OAuth client JSON.")
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN_PATH, help="Token file.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH, help="Sent log.")
    parser.add_argument("--auth", action="store_true", help="Run the OAuth consent flow.")
    parser.add_argument("--whoami", action="store_true", help="Print the authenticated Gmail account.")
    parser.add_argument("--send", action="store_true", help="Actually send. Without this, dry-run only.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive send confirmation.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum emails to send (0 = all).")
    parser.add_argument("--only", default="", help="Comma-separated business-name or email filters.")
    parser.add_argument("--sender-name", default="", help="Replace [Your name].")
    parser.add_argument("--sender-company", default="", help="Replace [Your company].")
    parser.add_argument("--sender-address", default="", help="Replace [Your postal address].")
    parser.add_argument("--from", dest="from_address", default="", help="Optional From address.")
    parser.add_argument("--unsubscribe", default="", help="Mailto address for List-Unsubscribe.")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds between sends.")
    parser.add_argument("--resend", action="store_true", help="Ignore the sent log.")
    parser.add_argument("--show-body", action="store_true", help="Print the full body in dry-run.")
    parser.add_argument("--no-browser", action="store_true", help="Print the auth URL without opening a browser.")
    parser.add_argument("--timeout", type=int, default=300, help="OAuth callback timeout in seconds.")
    parser.add_argument("--scopes", default="", help="Space-separated OAuth scopes (default: gmail.compose).")
    args = parser.parse_args()

    try:
        client = load_client(find_client_file(args.client_secret))
        scopes = [item for item in args.scopes.split() if item] or DEFAULT_SCOPES

        if args.auth:
            token = run_oauth_flow(
                client,
                token_path=args.token,
                scopes=scopes,
                open_browser=not args.no_browser,
                timeout=args.timeout,
            )
            if not args.whoami:
                return
            # Fall through to --whoami with the new token.

        if args.whoami:
            access_token = get_access_token(
                client,
                args.token,
                scopes=scopes,
                auth=False,
                open_browser=not args.no_browser,
                timeout=args.timeout,
            )
            profile = gmail_profile(access_token)
            print(f"Authenticated Gmail account: {profile.get('emailAddress', 'unknown')}")
            print(f"Messages total: {profile.get('messagesTotal', 'unknown')}")
            print(f"Threads total: {profile.get('threadsTotal', 'unknown')}")
            return

        if not args.csv:
            parser.error("--csv is required unless you use --auth or --whoami.")

        rows = load_ready_rows(args.csv, only=args.only, limit=args.limit)
        if not rows:
            print("No rows marked cold_email_status=ready with a published email.")
            return

        if args.send:
            problems = []
            for row in rows:
                body = apply_sender_placeholders(
                    row["body"],
                    sender_name=args.sender_name,
                    sender_company=args.sender_company,
                    sender_address=args.sender_address,
                )
                remaining = sorted(set(PLACEHOLDER_RE.findall(body)))
                if remaining:
                    problems.append(f"{row['business']}: {', '.join(remaining)}")
            if problems:
                print("[error] Sender placeholders remain. Pass --sender-name, --sender-company, and --sender-address.")
                for problem in problems:
                    print(f"  - {problem}")
                raise SystemExit(1)

        if not args.send:
            print(f"Dry run: {len(rows)} ready email(s). Nothing sent.")
            for index, row in enumerate(rows, 1):
                body = apply_sender_placeholders(
                    row["body"],
                    sender_name=args.sender_name,
                    sender_company=args.sender_company,
                    sender_address=args.sender_address,
                )
                remaining = PLACEHOLDER_RE.findall(body)
                print("")
                print(f"[{index}] {row['business']} <{row['email']}>")
                print(f"    Subject: {row['subject']}")
                if remaining:
                    print(f"    Placeholders remaining: {', '.join(sorted(set(remaining)))}")
                if args.show_body:
                    for line in body.splitlines():
                        print(f"    {line}")
            if not args.sender_name or not args.sender_address:
                print("")
                print("Pass --sender-name and --sender-address before sending.")
            print("")
            print("Run again with --send --yes to send.")
            return

        if not args.yes:
            if not sys.stdin.isatty():
                raise GmailSenderError("Sending requires --yes in non-interactive mode.")
            answer = input(f"Type SEND to send {len(rows)} email(s): ").strip()
            if answer != "SEND":
                print("Cancelled.")
                return

        sent_log = load_sent_log(args.log)
        access_token = get_access_token(
            client,
            args.token,
            scopes=scopes,
            auth=False,
            open_browser=not args.no_browser,
            timeout=args.timeout,
        )

        sent_count = 0
        skipped_count = 0
        for index, row in enumerate(rows, 1):
            body = apply_sender_placeholders(
                row["body"],
                sender_name=args.sender_name,
                sender_company=args.sender_company,
                sender_address=args.sender_address,
            )
            remaining = PLACEHOLDER_RE.findall(body)
            if remaining:
                print(
                    f"[skip] {row['business']}: placeholders remain "
                    f"({', '.join(sorted(set(remaining)))})."
                )
                skipped_count += 1
                continue
            key = row["email"].lower()
            if not args.resend and key in sent_log:
                print(f"[skip] {row['business']}: already sent on {sent_log[key].get('sent_at', 'unknown')}.")
                skipped_count += 1
                continue

            raw = build_raw_message(
                to=row["email"],
                subject=row["subject"],
                body=body,
                sender=args.from_address,
                unsubscribe=args.unsubscribe,
            )
            result = send_raw_message(access_token, raw)
            message_id = result.get("id", "")
            sent_log[key] = {
                "sent_at": utc_now(),
                "message_id": message_id,
                "business": row["business"],
                "subject": row["subject"],
            }
            write_private_json(args.log, sent_log)
            sent_count += 1
            print(f"[{index}/{len(rows)}] sent to {row['email']} (message {message_id}).")
            if args.delay and index < len(rows):
                time.sleep(args.delay)

        print("")
        print(f"Sent {sent_count}; skipped {skipped_count}.")
    except GmailSenderError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
