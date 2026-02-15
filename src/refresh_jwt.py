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
MAGIC_URL = os.getenv("MARVELOUS_MAGIC_URL")
SECONDARY_PASSWORD = os.getenv("MARVELOUS_SECONDARY_PASSWORD")

# File paths
JWT_CACHE_FILE = Path("/root/twy-announce/.jwt_cache.json")

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
    """Extract JWT token using Playwright with magic code URL."""
    if not MAGIC_URL or not SECONDARY_PASSWORD:
        raise ValueError("Missing MARVELOUS_MAGIC_URL or MARVELOUS_SECONDARY_PASSWORD in .env")
    
    print(f"Launching browser to fetch JWT for Report {report_id}...")
    
    with sync_playwright() as p:
        # Launch headless browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate directly to magic code URL
            print("Navigating to Marvelous dashboard with magic code...")
            page.goto(MAGIC_URL, wait_until="domcontentloaded", timeout=30000)
            
            # Wait a bit for page to load
            page.wait_for_timeout(3000)
            
            # Check if password challenge appears
            print("Checking for password challenge...")
            password_inputs = page.locator("input[type='password']").all()
            
            if len(password_inputs) > 0:
                print(f"Found {len(password_inputs)} password field(s), entering secondary password...")
                # Fill the first visible password field
                for pwd_input in password_inputs:
                    if pwd_input.is_visible():
                        pwd_input.fill(SECONDARY_PASSWORD)
                        break
                
                # Click submit button
                submit_buttons = page.locator("button[type='submit'], button:has-text('Submit'), button:has-text('Continue'), button:has-text('Verify')").all()
                for btn in submit_buttons:
                    if btn.is_visible():
                        print("Clicking submit button...")
                        btn.click()
                        break
                
                # Wait for URL to change (indicating successful auth)
                print("Waiting for authentication...")
                page.wait_for_timeout(5000)
            else:
                print("No password challenge, already authenticated")
            
            print(f"Current URL: {page.url}")
            
            # Navigate to report page
            report_url = f"https://app.heymarvelous.com/reports/users/{report_id}"
            print(f"Navigating to report page: {report_url}")
            page.goto(report_url, wait_until="domcontentloaded", timeout=30000)
            
            # Wait for page to render
            page.wait_for_timeout(5000)
            
            # Wait for iframe to load
            print("Waiting for report iframe to load...")
            iframe_locator = page.locator("iframe[src*='reports.heymarv.com']")
            iframe_locator.wait_for(timeout=20000)
            
            # Get iframe src attribute
            iframe_src = iframe_locator.get_attribute("src")
            print(f"Found iframe: {iframe_src[:80]}...")
            
            # Extract JWT from iframe src
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
