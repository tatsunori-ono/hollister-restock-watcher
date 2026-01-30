#!/usr/bin/env python3
"""
Restock watcher for Hollister product pages (EU/UK).

How it works:
- Opens the product page in a real browser (Playwright)
- Optionally selects a color and size
- Checks whether the "Add to Bag" (or "Add to Cart") button is enabled
- Sends a notification (Discord webhook and/or Email) only when it transitions from OOS -> IN STOCK
- Saves state to a local JSON file so you don't get spammed every check

Notes:
- E-commerce sites can change markup; this script uses multiple fallback strategies.
- Keep your polling interval reasonable (e.g., 2–10 minutes).
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional, Tuple

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ----------------------------
# CONFIG (edit these)
# ----------------------------

PRODUCT_URL = "https://www.hollisterco.com/shop/uk/p/lace-trim-layering-cami-61713322-1005"

# Set to None to skip picking a specific color/size.
DESIRED_COLOR_NAME = "cloud white"  # e.g. "cloud white" or "navy blue stripe"
DESIRED_SIZE = "M"       # e.g. "S" or "M" or "XS"

CHECK_EVERY_SECONDS = 180  # 3 minutes (keep it reasonable)

STATE_FILE = ".restock_state.json"

# Notifications (enable what you want)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# Email (optional)
EMAIL_ENABLED = True
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "").strip()

# export SMTP_HOST="smtp.gmail.com"
# export SMTP_PORT=587
# export SMTP_USERNAME="tatsunori.no1@gmail.com"
# export SMTP_PASSWORD="izvy qdnh jgeh tspv"
# export EMAIL_FROM="tatsunori.no1@gmail.com"
# export EMAIL_TO="tatsunori.no1@gmail.com"


# ----------------------------
# Helpers
# ----------------------------

@dataclass
class StockResult:
    in_stock: bool
    reason: str
    resolved_url: str
    color: Optional[str]
    size: Optional[str]


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def send_discord(webhook_url: str, content: str) -> None:
    if not webhook_url:
        return
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    r.raise_for_status()


def send_email(subject: str, body: str) -> None:
    if not EMAIL_ENABLED:
        return
    missing = [k for k, v in {
        "SMTP_HOST": SMTP_HOST,
        "SMTP_USERNAME": SMTP_USERNAME,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "EMAIL_FROM": EMAIL_FROM,
        "EMAIL_TO": EMAIL_TO,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Email enabled but missing env vars: {', '.join(missing)}")

    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls(context=context)
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)


def now_utc_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())


# ----------------------------
# Playwright logic
# ----------------------------

COOKIE_BUTTON_TEXT_CANDIDATES = [
    "Accept", "Accept All", "Allow all", "I Accept", "Agree", "OK",
    "Accept Cookies", "Allow All Cookies",
]


def try_click_cookie_banner(page) -> None:
    # Best-effort: click common cookie buttons if they exist.
    for txt in COOKIE_BUTTON_TEXT_CANDIDATES:
        try:
            loc = page.locator(f"button:has-text('{txt}')").first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=1500)
                return
        except Exception:
            pass


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def pick_color(page, color_name: str) -> Tuple[bool, str]:
    """
    Try multiple ways to select a color.
    Returns (success, message).
    """
    target = normalize(color_name)

    # Strategy A: click the color thumbnail image by alt text.
    try:
        img = page.locator(f"img[alt]").filter(has_text="").locator(f"xpath=..")
        # Above is generic; instead search all imgs and match alt
        imgs = page.locator("img[alt]")
        n = imgs.count()
        for i in range(n):
            alt = imgs.nth(i).get_attribute("alt") or ""
            if target in normalize(alt):
                # Often the clickable element is the parent button/link.
                try:
                    imgs.nth(i).click(timeout=2000)
                except Exception:
                    imgs.nth(i).locator("xpath=ancestor::button[1] | ancestor::a[1]").first.click(timeout=2000)
                return True, f"Selected color via image alt='{alt}'."
    except Exception:
        pass

    # Strategy B: find a button-like element with the color name
    try:
        btn = page.locator("button", has_text=re.compile(re.escape(color_name), re.I)).first
        if btn.count() > 0:
            btn.click(timeout=2000)
            return True, "Selected color via button text."
    except Exception:
        pass

    return False, f"Could not confidently select color '{color_name}'. (Site markup may have changed.)"


def pick_size(page, size: str) -> Tuple[bool, str]:
    """
    Try selecting a size (XXS, XS, S, M, etc).
    Returns (success, message).
    """
    size = size.strip().upper()

    # Prefer buttons with exact-ish text.
    candidates = [
        page.locator("button", has_text=re.compile(rf"^{re.escape(size)}$", re.I)),
        page.locator(f"button:has-text('{size}')"),
        page.locator(f"[role='button']:has-text('{size}')"),
    ]

    for loc in candidates:
        try:
            if loc.count() == 0:
                continue
            # If multiple, try the first visible enabled one
            for i in range(loc.count()):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                # Some sites mark as disabled via aria-disabled
                aria_disabled = (el.get_attribute("aria-disabled") or "").lower()
                disabled_attr = el.get_attribute("disabled")
                if aria_disabled == "true" or disabled_attr is not None:
                    continue
                el.click(timeout=2000)
                return True, f"Selected size {size}."
        except Exception:
            continue

    return False, f"Could not confidently select size '{size}'. (It might be out of stock or markup changed.)"


def find_add_to_bag_button(page):
    # Hollister often uses "Add to Bag"; sometimes "Add to Cart"
    for text in ["Add to Bag", "Add to Cart"]:
        loc = page.locator("button", has_text=re.compile(text, re.I)).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            pass
    return None


def is_in_stock_by_button(page) -> Tuple[bool, str]:
    """
    Determines stock by whether the add-to-bag button is enabled.
    Returns (in_stock, reason).
    """
    btn = find_add_to_bag_button(page)
    if btn is None or btn.count() == 0:
        # Fallback: page text signals
        text = normalize(page.inner_text("body"))
        for phrase in ["out of stock", "sold out", "currently unavailable"]:
            if phrase in text:
                return False, f"Detected phrase '{phrase}' in page text."
        return False, "Could not find Add to Bag/Add to Cart button."

    try:
        # is_enabled handles disabled attribute; some sites use aria-disabled
        if btn.is_enabled():
            aria_disabled = (btn.get_attribute("aria-disabled") or "").lower()
            if aria_disabled == "true":
                return False, "Add button present but aria-disabled=true."
            return True, "Add button is enabled."
        return False, "Add button is disabled."
    except Exception as e:
        return False, f"Error while checking add button enabled state: {e!r}"


def check_stock_once(url: str, desired_color: Optional[str], desired_size: Optional[str]) -> StockResult:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try_click_cookie_banner(page)

            # Give the client-side app a moment to hydrate/render.
            page.wait_for_timeout(1500)

            resolved_url = page.url

            color_used = desired_color
            size_used = desired_size

            if desired_color:
                ok, msg = pick_color(page, desired_color)
                # Small wait for UI update
                page.wait_for_timeout(800)
                if not ok:
                    # Not fatal; just record
                    color_used = desired_color
                # You can print msg for debugging
                # print(msg)

            if desired_size:
                ok, msg = pick_size(page, desired_size)
                page.wait_for_timeout(800)
                if not ok:
                    size_used = desired_size
                # print(msg)

            in_stock, reason = is_in_stock_by_button(page)

            return StockResult(
                in_stock=in_stock,
                reason=reason,
                resolved_url=resolved_url,
                color=color_used,
                size=size_used,
            )
        except PlaywrightTimeoutError:
            return StockResult(
                in_stock=False,
                reason="Timed out loading the page.",
                resolved_url=url,
                color=desired_color,
                size=desired_size,
            )
        finally:
            context.close()
            browser.close()


def format_key(url: str, color: Optional[str], size: Optional[str]) -> str:
    return f"{url} | color={color or '*'} | size={size or '*'}"


def main() -> int:

    send_email(
        "Restock watcher test",
        "If you received this email, SMTP is configured correctly."
    )

    key = format_key(PRODUCT_URL, DESIRED_COLOR_NAME, DESIRED_SIZE)
    state = load_state(STATE_FILE)

    print(f"[{now_utc_str()}] Watching: {key}")
    print(f"Check interval: {CHECK_EVERY_SECONDS}s")
    print(f"Discord enabled: {bool(DISCORD_WEBHOOK_URL)} | Email enabled: {EMAIL_ENABLED}")
    print("----")

    while True:
        try:
            result = check_stock_once(PRODUCT_URL, DESIRED_COLOR_NAME, DESIRED_SIZE)
            prev = state.get(key, {})
            prev_in_stock = bool(prev.get("in_stock", False))

            print(f"[{now_utc_str()}] in_stock={result.in_stock} reason={result.reason}")

            # Transition: OOS -> IN STOCK
            if result.in_stock and not prev_in_stock:
                msg = (
                    "✅ RESTOCK DETECTED!\n"
                    f"Product: {PRODUCT_URL}\n"
                    f"Open: {result.resolved_url}\n"
                    f"Color: {result.color or '(any)'} | Size: {result.size or '(any)'}\n"
                    f"Signal: {result.reason}\n"
                    f"Time: {now_utc_str()}"
                )
                print(msg)

                # Notify
                if DISCORD_WEBHOOK_URL:
                    send_discord(DISCORD_WEBHOOK_URL, msg)
                if EMAIL_ENABLED:
                    send_email("Restock detected!", msg)

            # Update state
            state[key] = {
                "in_stock": result.in_stock,
                "last_check_utc": now_utc_str(),
                "last_reason": result.reason,
                "last_resolved_url": result.resolved_url,
            }
            save_state(STATE_FILE, state)

        except Exception as e:
            print(f"[{now_utc_str()}] ERROR: {e!r}", file=sys.stderr)

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
