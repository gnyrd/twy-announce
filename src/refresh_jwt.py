#!/usr/bin/env python3
"""Refresh Marvelous JWT tokens using Playwright browser automation."""

import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration from environment
TWY_USERNAME = os.getenv("MARVELOUS_TWY_USERNAME")
TWY_PASSWORD = os.getenv("MARVELOUS_TWY_PASSWORD")
SECONDARY_PASSWORD = os.getenv("MARVELOUS_SECONDARY_PASSWORD")

# File paths
JWT_CACHE_FILE = Path(__file__).parent.parent / ".jwt_cache.json"

# Token validity buffer (refresh if expires within this time)
TOKEN_REFRESH_BUFFER_HOURS = 24


def decode_jwt_payload(jwt_token: str) -> dict:
    """Decode JWT payload without verification (just to read expiry)."""
    try:
        # JWT format: header.payload.signature
        parts = jwt_token.split('.')
        if len(parts) != 3:
            return None
        
        # Decode payload (add padding if needed)
        payload = parts[1]
        # Add padding if necessary
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        print(f"Warning: Could not decode JWT payload: {e}")
        return None


def is_cached_token_valid() -> bool:
    """Check if cached JWT token exists and is still valid."""
    if not JWT_CACHE_FILE.exists():
        print("No cached token found")
        return False
    
    try:
        # Load cached token
        with open(JWT_CACHE_FILE) as f:
            cache_data = json.load(f)
            jwt_token = cache_data.get("jwt_token")
        
        if not jwt_token:
            print("Cached token is empty")
            return False
        
        # Decode payload to check expiry
        payload = decode_jwt_payload(jwt_token)
        if not payload or 'exp' not in payload:
            print("Could not read token expiry")
            return False
        
        exp_timestamp = payload['exp']
        current_timestamp = time.time()
        buffer_seconds = TOKEN_REFRESH_BUFFER_HOURS * 3600
        
        # Check if token expires within buffer time
        time_until_expiry = exp_timestamp - current_timestamp
        
        if time_until_expiry > buffer_seconds:
            hours_remaining = time_until_expiry / 3600
            print(f"✓ Cached token is valid (expires in {hours_remaining:.1f} hours)")
            return True
        else:
            hours_remaining = time_until_expiry / 3600
            print(f"Cached token expires soon (in {hours_remaining:.1f} hours), refreshing...")
            return False
            
    except Exception as e:
        print(f"Error checking cached token: {e}")
        return False


def save_jwt(jwt_token: str, report_id: int):
    """Save JWT token to cache file."""
    cache_data = {
        "jwt_token": jwt_token,
        "report_id": report_id,
    }
    with open(JWT_CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)
    print(f"JWT cached to {JWT_CACHE_FILE}")


def extract_jwt_with_playwright(report_id: int = 56) -> str:
    """Extract JWT token using Playwright - recorded flow."""
    if not TWY_USERNAME or not TWY_PASSWORD or not SECONDARY_PASSWORD:
        raise ValueError("Missing credentials in .env")
    
    print(f"Launching browser to fetch JWT for Report {report_id}...")
    
    with sync_playwright() as p:
        # Launch headless browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to login page
            print("Navigating to login page...")
            page.goto("https://app.heymarvelous.com/login", wait_until="domcontentloaded", timeout=30000)
            
            # Fill email
            print("Filling email...")
            page.get_by_role("textbox", name="Email").click()
            page.get_by_role("textbox", name="Email").fill(TWY_USERNAME)
            
            # Fill password
            print("Filling password...")
            page.get_by_role("textbox", name="Password").click()
            page.get_by_role("textbox", name="Password").fill(TWY_PASSWORD)
            
            # Click login button
            print("Clicking login...")
            page.get_by_role("button", name="Log in").click()
            
            # Wait for secondary password challenge
            page.wait_for_timeout(3000)
            
            # Fill secondary password
            print("Filling secondary password...")
            page.get_by_role("textbox", name="Password").click()
            page.get_by_role("textbox", name="Password").fill(SECONDARY_PASSWORD)
            
            # Click unlock
            print("Clicking unlock...")
            page.get_by_role("button", name="Unlock").click()
            
            # Wait for dashboard
            page.wait_for_timeout(3000)
            
            # Navigate to Reports
            print("Navigating to Reports...")
            page.get_by_role("link", name="Reports").click()
            
            # Click Students section
            print("Opening Students section...")
            page.get_by_role("link", name="What are they up to? Students").click()
            
            # Click Active Subscriptions report
            print("Opening Active Subscriptions report...")
            page.get_by_role("link", name="Active Subscriptions by").click()
            
            # Wait for iframe to load
            print("Waiting for report iframe...")
            page.wait_for_timeout(5000)
            
            iframe_locator = page.locator("iframe[src*='reports.heymarv.com']")
            iframe_locator.wait_for(timeout=20000)
            
            # Get iframe src and extract JWT
            iframe_src = iframe_locator.get_attribute("src")
            print(f"Found iframe: {iframe_src[:80]}...")
            
            match = re.search(r'reports\.heymarv\.com/embed/question/(eyJ[^#?]+)', iframe_src)
            if match:
                jwt_token = match.group(1)
                print(f"✓ Extracted JWT (length: {len(jwt_token)})")
                return jwt_token
            else:
                print("✗ Could not extract JWT from iframe src")
                return None
                
        except PlaywrightTimeout as e:
            print(f"✗ Timeout: {e}")
            print(f"Current URL: {page.url}")
            return None
        except Exception as e:
            print(f"✗ Error: {e}")
            print(f"Current URL: {page.url}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            context.close()
            browser.close()


def main():
    """Main entry point."""
    report_id = 56  # Active Subscriptions by Product
    
    print("=" * 60)
    print("Marvelous JWT Refresh Script")
    print("=" * 60)
    
    # Check if cached token is still valid
    if is_cached_token_valid():
        print("\n✓ Using cached JWT token (still valid)")
        return 0
    
    # Token expired or doesn't exist, fetch new one
    print("\nFetching new JWT token...")
    jwt_token = extract_jwt_with_playwright(report_id)
    
    if jwt_token:
        save_jwt(jwt_token, report_id)
        print("\n✓ SUCCESS: JWT token refreshed")
        return 0
    else:
        print("\n✗ FAILED: Could not refresh JWT token")
        return 1


if __name__ == "__main__":
    sys.exit(main())
