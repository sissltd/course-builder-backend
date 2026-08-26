#!/usr/bin/env python3
"""Verify Cloudflare SMTP (or any configured SMTP) delivers.

Reads the same env vars the Django settings use:
    EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
    DEFAULT_FROM_EMAIL, EMAIL_USE_TLS, EMAIL_USE_SSL

Usage:
    python devscripts/test_smtp.py user@example.com
    python devscripts/test_smtp.py user@example.com --host smtp.cloudflare.com --port 465
"""

import argparse
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from decouple import config


def env(key: str, default: str = "") -> str:
    try:
        return config(key, default=default)
    except Exception:
        return default


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Send a test email via SMTP and verify delivery.")
    p.add_argument("to", help="Recipient email address to send the test to.")
    p.add_argument("--host", default=env("EMAIL_HOST", "smtp.cloudflare.com"),
                   help="SMTP server host (default: EMAIL_HOST env or smtp.cloudflare.com)")
    p.add_argument("--port", type=int, default=int(env("EMAIL_PORT", "465")),
                   help="SMTP server port (default: EMAIL_PORT env or 465)")
    p.add_argument("--user", default=env("EMAIL_HOST_USER"),
                   help="SMTP username (default: EMAIL_HOST_USER env)")
    p.add_argument("--password", default=env("EMAIL_HOST_PASSWORD"),
                   help="SMTP password/API token (default: EMAIL_HOST_PASSWORD env)")
    p.add_argument("--from-email", default=env("DEFAULT_FROM_EMAIL"),
                   help="Sender address (default: DEFAULT_FROM_EMAIL env)")
    p.add_argument("--tls", action="store_true", default=env("EMAIL_USE_TLS", "") == "True",
                   help="Use STARTTLS (default: EMAIL_USE_TLS env)")
    p.add_argument("--ssl", action="store_true", default=env("EMAIL_USE_SSL", "") == "True",
                   help="Use implicit SSL (default: EMAIL_USE_SSL env)")
    return p


def send_test(host: str, port: int, user: str, password: str,
              from_email: str, to_email: str, use_tls: bool, use_ssl: bool) -> dict:
    """Connect, authenticate, send one test message. Return a result dict."""

    result = {
        "host": f"{host}:{port}",
        "from": from_email,
        "to": to_email,
        "tls": "STARTTLS" if use_tls else ("SSL" if use_ssl else "none"),
        "status": None,
        "message": None,
    }

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SMTP test — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    msg["From"] = from_email
    msg["To"] = to_email

    body = (
        "This is an automated test from course-builder-backend.\n\n"
        f"Sent at: {datetime.now(timezone.utc).isoformat()}\n"
        f"Server: {host}:{port}\n"
        f"TLS: {'STARTTLS' if use_tls else ('SSL' if use_ssl else 'none')}\n\n"
        "If you received this, your Cloudflare SMTP configuration is working."
    )
    msg.attach(MIMEText(body, "plain"))

    try:
        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if use_tls:
                server.starttls(context=ssl.create_default_context())

        server.ehlo()
        server.login(user, password)
        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()

        result["status"] = "OK"
        result["message"] = "Email sent successfully."
    except smtplib.SMTPAuthenticationError as exc:
        result["status"] = "AUTH FAILED"
        result["message"] = str(exc)
    except smtplib.SMTPConnectError as exc:
        result["status"] = "CONNECT FAILED"
        result["message"] = str(exc)
    except smtplib.SMTPServerDisconnected as exc:
        result["status"] = "DISCONNECTED"
        result["message"] = str(exc)
    except smtplib.SMTPException as exc:
        result["status"] = "SMTP ERROR"
        result["message"] = str(exc)
    except OSError as exc:
        result["status"] = "NETWORK ERROR"
        result["message"] = str(exc)

    return result


def main():
    args = build_parser().parse_args()

    missing = []
    if not args.user:
        missing.append("EMAIL_HOST_USER")
    if not args.password:
        missing.append("EMAIL_HOST_PASSWORD")
    if not args.from_email:
        missing.append("DEFAULT_FROM_EMAIL")
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        print("Set them in your .env or export them before running.")
        sys.exit(1)

    print(f"Connecting to {args.host}:{args.port} ...")
    print(f"  User:   {args.user}")
    print(f"  From:   {args.from_email}")
    print(f"  To:     {args.to}")
    print(f"  TLS:    {'STARTTLS' if args.tls else ('SSL' if args.ssl else 'none')}")
    print()

    result = send_test(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        from_email=args.from_email,
        to_email=args.to,
        use_tls=args.tls,
        use_ssl=args.ssl,
    )

    if result["status"] == "OK":
        print(f"  Status:  {result['status']}")
        print(f"  Message: {result['message']}")
        sys.exit(0)
    else:
        print(f"  Status:  {result['status']}")
        print(f"  Error:   {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
