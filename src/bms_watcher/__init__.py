"""Watch a BookMyShow movie page and email when ticket booking opens."""

import argparse
import os
import random
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

JITTER_MAX = 60
MAX_FAILS_IN_A_ROW = 5
ALERT_COUNT = 3
ALERT_GAP = 20
BOOK_RE = re.compile(r"^\s*book\s+tickets\s*$", re.IGNORECASE)
INTERESTED_RE = re.compile(r"^\s*(?:i(?:'|’)?m\s+)?interested\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    gmail_address: str = ""
    gmail_app_password: str = ""
    alert_to: str = ""
    watch_url: str = ""
    check_interval: int = 180
    headless: bool = False


def load_settings(env_file):
    """Read only the selected file; exported environment variables take precedence."""
    values = {**dotenv_values(env_file, interpolate=False), **os.environ}

    def get(name, default=""):
        return (values.get(name) or default).strip()

    try:
        interval = int(get("CHECK_INTERVAL", "180"))
    except ValueError:
        raise ValueError("CHECK_INTERVAL must be an integer number of seconds.") from None
    if interval < 180:
        raise ValueError("CHECK_INTERVAL must be at least 180 seconds.")
    headless = get("HEADLESS", "false").lower()
    if headless not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError("HEADLESS must be true or false (also accepts 1/0 or yes/no).")
    address = get("GMAIL_ADDRESS")
    return Settings(
        gmail_address=address,
        gmail_app_password=get("GMAIL_APP_PASSWORD").replace(" ", ""),
        alert_to=get("ALERT_TO", address),
        watch_url=get("WATCH_URL"),
        check_interval=interval,
        headless=headless in {"true", "1", "yes"},
    )


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def send_email(settings, subject, body):
    """Send through Gmail SSL, falling back to STARTTLS on transport errors."""
    msg = EmailMessage()
    msg["From"] = settings.gmail_address
    msg["To"] = settings.alert_to
    msg["Subject"] = subject
    msg.set_content(body)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise
    except (OSError, smtplib.SMTPException) as error:
        log(f"SMTP over SSL (465) failed: {error}. Trying STARTTLS (587)...")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(msg)


def try_send_email(settings, subject, body):
    try:
        send_email(settings, subject, body)
        log(f"Email sent: {subject}")
        return True
    except Exception as error:
        log(f"Could not send email '{subject}': {error}")
        return False


def has_visible_text(page, pattern):
    matches = page.get_by_text(pattern)
    return any(matches.nth(i).is_visible() for i in range(matches.count()))


def booking_is_open(page, url):
    """Return booking status; raise when neither exact status label is visible."""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(3_000)
    if has_visible_text(page, BOOK_RE):
        return True
    if has_visible_text(page, INTERESTED_RE):
        return False
    raise RuntimeError(
        f'Found neither "Book tickets" nor "Interested" (page title: {page.title()!r}). '
        "The page may be blocked or not fully loaded."
    )


def close_quietly(browser):
    try:
        if browser:
            browser.close()
    except Exception:
        pass


def wait_for_next_check(settings):
    wait = settings.check_interval + random.uniform(0, JITTER_MAX)
    log(f"Waiting {wait:.0f}s.")
    time.sleep(wait)


def send_booking_alerts(settings, url):
    """Retry complete batches until at least one alert is accepted by SMTP."""
    while True:
        sent = 0
        for n in range(1, ALERT_COUNT + 1):
            sent += try_send_email(
                settings,
                f"BOOKINGS OPEN on BookMyShow! (alert {n}/{ALERT_COUNT})",
                f'The "Book tickets" button is live.\n\n{url}\n',
            )
            if n < ALERT_COUNT:
                time.sleep(ALERT_GAP)
        if sent:
            return
        log("All booking alerts failed. Booking is remembered; retrying email after the wait.")
        wait_for_next_check(settings)


def check_once(url, headless):
    """One page check, no email and no email credentials required."""
    log(f"Test check: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            is_open = booking_is_open(browser.new_page(), url)
        finally:
            close_quietly(browser)
    log('DETECTED: "Book tickets" is visible.' if is_open
        else 'Not open: "Interested" is visible.')


def watch(settings, headless):
    url = settings.watch_url
    log(f"Watching {url}")
    log(f"Checking every ~{settings.check_interval}s plus 0-{JITTER_MAX}s jitter. Ctrl+C to stop.")
    if not try_send_email(settings, "BMS watcher: bot started",
                          f"The watcher is running and email works.\n\nWatching: {url}\n"):
        raise RuntimeError("Startup email failed. Fix email and retry.")
    fails = checks = 0
    failure_notified = False
    with sync_playwright() as p:
        browser = page = None
        try:
            while True:
                checks += 1
                try:
                    if page is None:
                        browser = p.chromium.launch(headless=headless)
                        page = browser.new_page()
                    is_open = booking_is_open(page, url)
                    fails = 0
                    failure_notified = False
                except Exception as error:
                    fails += 1
                    log(f"Check #{checks} FAILED ({fails} in a row): {error}")
                    close_quietly(browser)
                    browser = page = None
                    if fails >= MAX_FAILS_IN_A_ROW and not failure_notified:
                        failure_notified = try_send_email(
                            settings, "BMS watcher: bot is struggling",
                            f"{fails} checks have failed in a row. I'll keep trying.\n\n"
                            f"Last error:\n{error}\n\nPage: {url}\n",
                        )
                else:
                    if is_open:
                        log(f"Check #{checks}: BOOKINGS ARE OPEN!")
                        close_quietly(browser)
                        browser = page = None
                        send_booking_alerts(settings, url)
                        return
                    log(f"Check #{checks}: not open yet.")
                wait_for_next_check(settings)
        finally:
            close_quietly(browser)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"),
                        help="configuration file (default: .env in the current directory)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--test-url", metavar="URL", help="check once without sending email")
    modes.add_argument("--test-email", action="store_true", help="send one test email and exit")
    parser.add_argument("--headless", action="store_true", help="hide the Chromium window")
    args = parser.parse_args(argv)
    if args.env_file != Path(".env") and not args.env_file.is_file():
        parser.error(f"Configuration file does not exist: {args.env_file}")
    try:
        settings = load_settings(args.env_file)
    except ValueError as error:
        parser.error(str(error))
    headless = settings.headless or args.headless
    if args.test_url:
        check_once(args.test_url, headless)
        return
    if not settings.gmail_address or not settings.gmail_app_password:
        parser.error("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in your .env or environment.")
    if args.test_email:
        send_email(settings, "BMS watcher: test email", "If you can read this, email alerts work.")
        log("Test email sent.")
        return
    parsed = urlparse(settings.watch_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parser.error("Set WATCH_URL to a complete http(s) BookMyShow movie URL.")
    watch(settings, headless)


def main():
    try:
        run()
    except KeyboardInterrupt:
        log("Stopped.")
    except Exception as error:
        sys.exit(f"BMS watcher failed: {error}")
