#!/usr/bin/env python3
"""Refresh Marvelous JWT tokens using Playwright browser automation."""

import json
import os
import re
import sys
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
