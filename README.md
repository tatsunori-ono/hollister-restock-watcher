---

# Hollister Restock Watcher

A Python-based restock monitoring tool for Hollister product pages.
The script periodically checks whether a specific product (optionally filtered by color and size) becomes available and sends a notification when a restock is detected.

The project uses Playwright to reliably handle client-side rendered pages and supports email notifications via SMTP.

---

## Features

* Monitors Hollister UK/EU product pages
* Optional targeting of a specific **color** and **size**
* Detects restock events based on purchase button availability
* Email notifications via SMTP (e.g. Gmail, Outlook)
* Prevents duplicate alerts using persistent local state
* Configurable polling interval
* Designed to be extensible (Discord, Telegram, API-based checks)

---

## Requirements

* Python 3.9+
* macOS, Linux, or Windows
* Internet connection

### Python dependencies

* `playwright`
* `requests` (for optional webhook support)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/hollister-restock-watcher.git
cd hollister-restock-watcher
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

(Windows)

```bat
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install playwright requests
```

### 4. Install Playwright browsers

```bash
python -m playwright install chromium
```

---

## Configuration

All configuration is done via environment variables and a small set of constants inside the script.

### Product configuration (in `restock_watcher.py`)

```python
PRODUCT_URL = "https://www.hollisterco.com/shop/uk/p/..."
DESIRED_COLOR_NAME = "cloud white"  # or None
DESIRED_SIZE = "M"                  # or None
CHECK_EVERY_SECONDS = 180
```

If `DESIRED_COLOR_NAME` or `DESIRED_SIZE` is set to `None`, the script will alert when *any* purchasable variant becomes available.

---

## Email Notifications (SMTP)

Email notifications are enabled directly in the script:

```python
EMAIL_ENABLED = True
```

### Required environment variables

Example for Gmail:

```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT=587
export SMTP_USERNAME="yourgmail@gmail.com"
export SMTP_PASSWORD="YOUR_APP_PASSWORD"
export EMAIL_FROM="yourgmail@gmail.com"
export EMAIL_TO="yourgmail@gmail.com"
```

Notes:

* Gmail requires an **App Password** (2-step verification enabled).
* Credentials are not stored in code and should never be committed.

---

## Running the Watcher

```bash
python3 restock_watcher.py
```

Example output:

```text
[2026-01-30 14:05:09Z] Watching: https://www.hollisterco.com/...
Check interval: 180s
Discord enabled: False | Email enabled: True
----
[2026-01-30 14:08:14Z] in_stock=False reason=Add button is disabled.
```

When a restock is detected, an email is sent immediately.

---

## State Management

The script stores its last known state in a local file:

```text
.restock_state.json
```

This prevents duplicate notifications when the item remains in stock across multiple checks.

---

## Debugging

If the script cannot detect the purchase button:

1. Run Playwright in non-headless mode:

```python
browser = p.chromium.launch(headless=False, slow_mo=200)
```

2. Enable screenshots to inspect page state:

```python
page.screenshot(path="debug_loaded.png", full_page=True)
```

This is useful for handling cookie banners, region selectors, or UI changes.

---

## Limitations

* The script relies on frontend structure, which may change over time.
* Excessive polling may violate site terms; use reasonable intervals.
* This project is for personal and educational use.

---

## Roadmap / Possible Extensions

* API-level stock detection (more robust, faster)
* Discord / Telegram notifications
* Multiple product monitoring
* Docker container support
* Background service / cron deployment

---

## License

MIT License.

---
