# BMS watcher

A small, installable Python tool that watches a BookMyShow movie page and emails
you when **Book tickets** appears. Each user supplies their own movie URL and Gmail
credentials in a private `.env` file. No credentials or movie are bundled.

The watcher checks every 180 seconds plus 0–60 seconds of random jitter. It sends a
startup email, watches until booking opens, then attempts three alert emails 20
seconds apart. If every alert fails, it remembers the opening and retries email
in later batches until at least one message is accepted by Gmail. SMTP acceptance
does not guarantee inbox placement; check your spam folder during setup.

## 1. Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone or
download this repository. Run the following from its root:

```bash
uv sync --locked
uv run playwright install chromium
```

The package requires Python 3.10 or newer; uv can download a suitable Python.
Playwright needs its Chromium browser installed separately. On Linux, use
`uv run playwright install --with-deps chromium` if system dependencies are missing.

## 2. Configure your own .env

```bash
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env`.

Create a Gmail App Password:

1. Enable **2-Step Verification** in your [Google account security settings](https://myaccount.google.com/security).
2. Open [App Passwords](https://myaccount.google.com/apppasswords) and create one
   for this watcher. Some managed accounts or security configurations do not
   support App Passwords.
3. Put your Gmail address and generated App Password into `.env`. Use the App
   Password, not your regular Google password.
4. Open your movie on BookMyShow, select your city, and copy its movie-page URL
   into `WATCH_URL`.

```dotenv
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=your-generated-app-password
ALERT_TO=
WATCH_URL=https://in.bookmyshow.com/movies/YOUR-CITY/YOUR-MOVIE/ET00000000
CHECK_INTERVAL=180
HEADLESS=false
```

The URL above is a placeholder; replace it with a real movie page. Leave `ALERT_TO`
blank to email yourself, or supply one other recipient address.

By default, only `.env` in the current directory is read. Parent directories are
not searched. Exported environment variables override file values. Use an explicit
path when launching from another directory:

```bash
uv run bms-watcher --env-file /absolute/path/to/your/.env
```

`.env` and alternate `.env.*` files are git-ignored.
`.env.example` is a shareable template. Never add real credentials to it.

## 3. Test your setup

Send one real test email:

```bash
uv run bms-watcher --test-email
```

Check detection against a movie already booking in your city. This mode sends
**no email** and needs no Gmail credentials:

```bash
uv run bms-watcher --test-url "YOUR_REAL_MOVIE_URL"
```

Expect `DETECTED: "Book tickets" is visible.` A page with an exact `Interested` or
`I'm interested` status is classified as not open. Missing status labels cause an
error rather than silently counting as a successful check. Detection is based on
visible text, not an attempt to purchase tickets.

Both test modes accept `--env-file`; add `--headless` to hide the browser window.

## 4. Start watching

```bash
uv run bms-watcher
```

Leave the Chromium window open. Stop with Ctrl+C. To keep a Mac awake while running:

```bash
caffeinate -dis uv run bms-watcher
```

Keep the laptop plugged in and its lid open. On Windows, disable sleep while
plugged in or use PowerToys Awake.

After five consecutive page failures, the watcher attempts a struggling email and
continues checking. A failed struggling email is retried on subsequent failed
checks. Successful page checks reset this failure state. The site may block
automation, and its labels may change; this tool cannot guarantee detection.

## Settings and options

| Setting | Default | Meaning |
| --- | --- | --- |
| `GMAIL_ADDRESS` | Required | Gmail sender and SMTP login |
| `GMAIL_APP_PASSWORD` | Required | Gmail App Password; spaces are removed |
| `ALERT_TO` | Sender address | One recipient for startup, health, and booking emails |
| `WATCH_URL` | Required for watching | Your city-specific movie page |
| `CHECK_INTERVAL` | `180` | At least 180 seconds, plus random jitter |
| `HEADLESS` | `false` | Hide Chromium; may make blocking more likely |

`--headless` enables headless mode for one invocation. `--test-email` and
`--test-url URL` are mutually exclusive. Run `uv run bms-watcher --help` for usage.

## Install the command or build distributions

After the setup above, optionally install the command outside the checkout:

```bash
uv tool install .
bms-watcher --env-file /absolute/path/to/your/.env
```

This installs from local source; it does not require or imply a PyPI release.
Continue to run the browser-install step after updating Playwright.

Build a wheel and source archive for sharing:

```bash
uv build
```

Artifacts are written to `dist/`. Keep real `.env` files and credentials out of
shared packages.

## Development checks

```bash
uv run python -B -m unittest discover -s tests -v
```

Tests use temporary configuration, simulated page text, and mocked email/browser
calls. They do not read your real `.env`, contact BookMyShow, or send email. Use
`--test-url` separately to check real site markup, which can change over time.

## License

MIT. See [LICENSE](LICENSE).
