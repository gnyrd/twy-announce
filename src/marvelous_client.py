"""Client library for HeyMarvelous (Namastream) API.

This module provides a Python interface for interacting with the
undocumented HeyMarvelous/Namastream API for event management.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Optional

import requests


class MarvelousAPIError(Exception):
    """Base exception for Marvelous API errors."""
    pass


class MarvelousAuthError(MarvelousAPIError):
    """Authentication-related errors."""
    pass


class MarvelousClient:
    """Client for HeyMarvelous (Namastream) API.
    
    Usage:
        # Initialize with token
        client = MarvelousClient(auth_token="your-token-here")
        
        # Or authenticate
        client = MarvelousClient()
        client.authenticate(email="user@example.com", password="password", magic_code="123456")
        
        # List events
        events = client.list_events(studio_slug="tiffany-wood-yoga")
        
        # Get single event
        event = client.get_event(event_id=12345)
        
        # Create event
        event_id = client.create_event(
            event_name="New Class",
            event_start_datetime="2026-01-25T16:00:00.000Z",
            event_end_datetime="2026-01-25T17:00:00.000Z",
            ...
        )
        
        # Update event
        client.update_event(event_id=12345, event_name="Updated Name")
        
        # Delete event
        client.delete_event(event_id=12345)
    """
    
    BASE_URL = "https://api.namastream.com"
    
    def __init__(self, auth_token: Optional[str] = None):
        """Initialize the client.
        
        Args:
            auth_token: Optional authentication token. If not provided,
                       call authenticate() before making authenticated requests.
        """
        self.auth_token = auth_token
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json",
        })
    
    def authenticate(self, email: str, password: str, magic_code: str) -> dict[str, Any]:
        """Authenticate and obtain API token.
        
        This is a two-step process:
        1. Login with email/password (triggers magic code email)
        2. Verify with magic code to get token
        
        Args:
            email: User email address
            password: User password
            magic_code: Magic code received via email
            
        Returns:
            Authentication response with 'key' and 'user_type'
            
        Raises:
            MarvelousAuthError: If authentication fails
        """
        # Step 1: Login
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/auth/login/",
                json={"email": email, "password": password},
                timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MarvelousAuthError(f"Login failed: {e}")
        
        # Step 2: Verify magic code
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/auth/magic-code/",
                json={"magic_code": magic_code},
                timeout=15
            )
            resp.raise_for_status()
            auth_data = resp.json()
            
            self.auth_token = auth_data.get("key")
            if not self.auth_token:
                raise MarvelousAuthError("No token returned in auth response")
            
            return auth_data
            
        except requests.RequestException as e:
            raise MarvelousAuthError(f"Magic code verification failed: {e}")
    
    def _get_auth_headers(self) -> dict[str, str]:
        """Get headers with authentication."""
        if not self.auth_token:
            raise MarvelousAuthError("No auth token available. Call authenticate() first.")
        
        return {
            "Authorization": f"Token {self.auth_token}",
            "Content-Type": "application/json",
        }
    
    def list_events(self, studio_slug: str) -> list[dict[str, Any]]:
        """List all events for a studio (public endpoint).
        
        Args:
            studio_slug: Studio slug (e.g., "tiffany-wood-yoga")
            
        Returns:
            List of event objects
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/studios/{studio_slug}/events",
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to list events: {e}")
    
    def get_event(self, event_id: int) -> dict[str, Any]:
        """Get single event details (requires authentication).
        
        Args:
            event_id: Event ID
            
        Returns:
            Event object
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/events/{event_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get event {event_id}: {e}")
    
    def create_event(
        self,
        event_name: str,
        event_start_datetime: str,
        event_end_datetime: str,
        date: str,
        start_time: str,
        duration_hours: int,
        duration_minutes: int,
        instructors: list[int],
        products: list[int],
        event_type: str = "collaborative_group_event",
        event_description: Optional[str] = None,
        **kwargs: Any
    ) -> int:
        """Create a new event.
        
        Args:
            event_name: Event name/title
            event_start_datetime: Start datetime in ISO 8601 format
            event_end_datetime: End datetime in ISO 8601 format
            date: Date string (YYYY-MM-DD)
            start_time: Time string (HH:MM)
            duration_hours: Duration hours
            duration_minutes: Duration minutes
            instructors: List of instructor IDs
            products: List of product IDs
            event_type: Event type (default: "collaborative_group_event")
            event_description: Optional plain text description (will be converted to EditorJS format)
            **kwargs: Additional event fields
            
        Returns:
            Created event ID
            
        Raises:
            MarvelousAPIError: If creation fails
        """
        payload = {
            "event_name": event_name,
            "event_type": event_type,
            "event_start_datetime": event_start_datetime,
            "event_end_datetime": event_end_datetime,
            "date": date,
            "start_time": start_time,
            "duration_hours": duration_hours,
            "duration_minutes": duration_minutes,
            "instructors": instructors,
            "products": products,
            # Defaults
            "registration_required": False,
            "post_event_recording_available": True,
            "recording_expiration_days": 3,
            "registration_limit": 0,
            "registration_closes_minutes_before_start": 0,
            "unregistration_closes_minutes_before_start": 0,
            "days_of_week": [],
            "agreed_to_www_requirements": False,
            "is_repeating_event": False,
            "is_free_event": False,
            "require_first_name": False,
            "require_last_name": False,
            "hide_registration_info": False,
            "email_notifications_enabled": True,
            "sms_notifications_enabled": True,
            "is_punchable_event": True,
            "attachments": [],
            "auto_notify_waitlist": True,
            "copy_event_description_to_recording": True,
            "student": None,
        }
        
        # Add description if provided
        if event_description:
            payload["event_description_new"] = self._create_description(event_description)
        
        # Override with any additional kwargs
        payload.update(kwargs)
        
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/api/events",
                headers=self._get_auth_headers(),
                json=payload,
                timeout=15
            )
            resp.raise_for_status()
            event = resp.json()
            return event["id"]
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to create event: {e}")
    
    def update_event(self, event_id: int, **updates: Any) -> dict[str, Any]:
        """Update an existing event.
        
        This method handles the complexity of converting nested objects to IDs.
        
        Args:
            event_id: Event ID to update
            **updates: Fields to update (e.g., event_name="New Name")
            
        Returns:
            Updated event object
            
        Raises:
            MarvelousAPIError: If update fails
        """
        # Get current event
        event = self.get_event(event_id)
        
        # Prepare for update (convert nested objects to IDs)
        event = self._prepare_event_for_update(event)
        
        # Apply updates
        event.update(updates)
        
        try:
            resp = self.session.put(
                f"{self.BASE_URL}/api/events/{event_id}",
                headers=self._get_auth_headers(),
                json=event,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to update event {event_id}: {e}")
    
    def delete_event(self, event_id: int) -> None:
        """Delete an event.
        
        Args:
            event_id: Event ID to delete
            
        Raises:
            MarvelousAPIError: If deletion fails
        """
        try:
            resp = self.session.delete(
                f"{self.BASE_URL}/api/events/{event_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to delete event {event_id}: {e}")
    
    @staticmethod
    def _prepare_event_for_update(event: dict[str, Any]) -> dict[str, Any]:
        """Prepare an event object from GET for PUT request.
        
        Converts nested objects (products, instructors) to ID arrays.
        """
        if isinstance(event.get('products'), list):
            event['products'] = [
                p['id'] if isinstance(p, dict) else p 
                for p in event['products']
            ]
        
        if isinstance(event.get('instructors'), list):
            event['instructors'] = [
                i['id'] if isinstance(i, dict) else i 
                for i in event['instructors']
            ]
        
        if isinstance(event.get('substitute_instructors'), list):
            event['substitute_instructors'] = [
                i['id'] if isinstance(i, dict) else i 
                for i in event['substitute_instructors']
            ]
        
        return event
    
    @staticmethod
    def _create_description(text: str) -> str:
        """Create EditorJS format description from plain text.
        
        Args:
            text: Plain text description (can include HTML tags)
            
        Returns:
            JSON string in EditorJS format
        """
        return json.dumps({
            "time": int(time.time() * 1000),
            "blocks": [{
                "id": f"para-{int(time.time())}",
                "type": "paragraph",
                "data": {"text": text},
                "tunes": {"alignmentTuneTool": {"alignment": "left"}}
            }],
            "version": "2.26.5"
        })
    
    @staticmethod
    def create_rich_description(blocks: list[dict[str, Any]]) -> str:
        """Create EditorJS format description with custom blocks.
        
        Args:
            blocks: List of EditorJS blocks
            
        Example:
            blocks = [
                {
                    "id": "header-1",
                    "type": "header",
                    "data": {"text": "My Header", "level": 3},
                    "tunes": {"alignmentTuneTool": {"alignment": "left"}}
                },
                {
                    "id": "para-1",
                    "type": "paragraph",
                    "data": {"text": "Paragraph text"},
                    "tunes": {"alignmentTuneTool": {"alignment": "left"}}
                }
            ]
            description = MarvelousClient.create_rich_description(blocks)
            
        Returns:
            JSON string in EditorJS format
        """
        return json.dumps({
            "time": int(time.time() * 1000),
            "blocks": blocks,
            "version": "2.26.5"
        })

    # ========== PRODUCT MANAGEMENT ==========
    
    def get_product(self, product_id: int) -> dict[str, Any]:
        """Get single product details (requires authentication).
        
        Args:
            product_id: Product ID
            
        Returns:
            Product object
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/products/{product_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get product {product_id}: {e}")
    
    def create_product(
        self,
        product_name: str,
        product_type: str = "media_library",
        **kwargs: Any
    ) -> int:
        """Create a new product.
        
        Args:
            product_name: Product name/title
            product_type: Product type (default: "media_library")
            **kwargs: Additional product fields
            
        Returns:
            Created product ID
            
        Raises:
            MarvelousAPIError: If creation fails
        """
        payload = {
            "product_name": product_name,
            "product_type": product_type,
        }
        
        # Override with any additional kwargs
        payload.update(kwargs)
        
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/api/products",
                headers=self._get_auth_headers(),
                json=payload,
                timeout=15
            )
            resp.raise_for_status()
            product = resp.json()
            return product["id"]
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to create product: {e}")
    
    def update_product(self, product_id: int, **updates: Any) -> dict[str, Any]:
        """Update an existing product.
        
        This method handles the complexity of converting nested objects to IDs.
        
        Args:
            product_id: Product ID to update
            **updates: Fields to update (e.g., product_name="New Name")
            
        Returns:
            Updated product object
            
        Raises:
            MarvelousAPIError: If update fails
        """
        # Get current product
        product = self.get_product(product_id)
        
        # Prepare for update (convert nested objects to IDs)
        product = self._prepare_product_for_update(product)
        
        # Apply updates
        product.update(updates)
        
        try:
            resp = self.session.put(
                f"{self.BASE_URL}/api/products/{product_id}",
                headers=self._get_auth_headers(),
                json=product,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to update product {product_id}: {e}")
    
    def delete_product(self, product_id: int) -> None:
        """Delete a product.
        
        Args:
            product_id: Product ID to delete
            
        Raises:
            MarvelousAPIError: If deletion fails
        """
        try:
            resp = self.session.delete(
                f"{self.BASE_URL}/api/products/{product_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to delete product {product_id}: {e}")
    
    def list_product_tags(self) -> list[dict[str, Any]]:
        """List available product tags.
        
        Returns:
            List of tag objects
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/product-tags",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to list product tags: {e}")
    
    @staticmethod
    def _prepare_product_for_update(product: dict[str, Any]) -> dict[str, Any]:
        """Prepare a product object from GET for PUT request.
        
        Converts nested objects (tags) to ID arrays.
        """
        if isinstance(product.get('tags'), list):
            product['tags'] = [
                t['id'] if isinstance(t, dict) else t 
                for t in product['tags']
            ]
        
        return product

    # ========== COUPON MANAGEMENT ==========
    
    def get_coupon(self, coupon_id: int) -> dict[str, Any]:
        """Get single coupon details (requires authentication).
        
        Args:
            coupon_id: Coupon ID
            
        Returns:
            Coupon object
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/coupons-paginated/{coupon_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get coupon {coupon_id}: {e}")
    
    def list_coupons(self, page: int = 1, search: str = "") -> dict[str, Any]:
        """List coupons with pagination.
        
        Args:
            page: Page number (default: 1)
            search: Search query (default: "")
            
        Returns:
            Paginated response with 'count', 'next', 'previous', 'results'
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/coupons-paginated",
                params={"page": page, "q": search},
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to list coupons: {e}")
    
    def get_coupon_stats(self) -> dict[str, Any]:
        """Get coupon statistics.
        
        Returns:
            Dict with 'total_coupons', 'used', 'total_revenue'
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/coupons-paginated/stats",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get coupon stats: {e}")
    
    def create_coupon(
        self,
        code: str,
        name: str,
        discount_amount: str,
        discount_type: str = "percentage",
        products: Optional[list[int]] = None,
        redeem_start: Optional[str] = None,
        redeem_end: Optional[str] = None,
        max_redemptions: Optional[str] = None,
        duration_type: str = "unlimited",
        duration_units: str = "1",
        **kwargs: Any
    ) -> int:
        """Create a new coupon.
        
        Args:
            code: Coupon code
            name: Coupon name/description
            discount_amount: Discount amount (e.g., "50" for 50%)
            discount_type: "percentage" or "fixed" (default: "percentage")
            products: List of product IDs (optional)
            redeem_start: Start date YYYY-MM-DD (optional)
            redeem_end: End date YYYY-MM-DD (optional)
            max_redemptions: Max number of uses (optional)
            duration_type: "unlimited" or other (default: "unlimited")
            duration_units: Duration units (default: "1")
            **kwargs: Additional coupon fields
            
        Returns:
            Created coupon ID
            
        Raises:
            MarvelousAPIError: If creation fails
        """
        payload = {
            "code": code,
            "name": name,
            "discount_amount": discount_amount,
            "discount_type": discount_type,
            "duration_type": duration_type,
            "duration_units": duration_units,
        }
        
        if products:
            payload["products"] = products
        if redeem_start:
            payload["redeem_start"] = redeem_start
        if redeem_end:
            payload["redeem_end"] = redeem_end
        if max_redemptions:
            payload["max_redemptions"] = max_redemptions
        
        # Override with any additional kwargs
        payload.update(kwargs)
        
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/api/coupons-paginated",
                headers=self._get_auth_headers(),
                json=payload,
                timeout=15
            )
            resp.raise_for_status()
            coupon = resp.json()
            return coupon["id"]
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to create coupon: {e}")
    
    def update_coupon(self, coupon_id: int, **updates: Any) -> dict[str, Any]:
        """Update an existing coupon.
        
        This method handles the complexity of converting nested objects to IDs.
        
        Args:
            coupon_id: Coupon ID to update
            **updates: Fields to update (e.g., discount_amount="30")
            
        Returns:
            Updated coupon object
            
        Raises:
            MarvelousAPIError: If update fails
        """
        # Get current coupon
        coupon = self.get_coupon(coupon_id)
        
        # Prepare for update (convert nested objects to IDs)
        coupon = self._prepare_coupon_for_update(coupon)
        
        # Apply updates
        coupon.update(updates)
        
        try:
            resp = self.session.put(
                f"{self.BASE_URL}/api/coupons-paginated/{coupon_id}",
                headers=self._get_auth_headers(),
                json=coupon,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to update coupon {coupon_id}: {e}")
    
    def delete_coupon(self, coupon_id: int) -> None:
        """Delete a coupon.
        
        Args:
            coupon_id: Coupon ID to delete
            
        Raises:
            MarvelousAPIError: If deletion fails
        """
        try:
            resp = self.session.delete(
                f"{self.BASE_URL}/api/coupons-paginated/{coupon_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to delete coupon {coupon_id}: {e}")
    
    @staticmethod
    def _prepare_coupon_for_update(coupon: dict[str, Any]) -> dict[str, Any]:
        """Prepare a coupon object from GET for PUT request.
        
        Converts nested objects (products) to ID arrays.
        """
        if isinstance(coupon.get('products'), list):
            coupon['products'] = [
                p['id'] if isinstance(p, dict) else p 
                for p in coupon['products']
            ]
        
        return coupon

    # ========== CUSTOMER MANAGEMENT ==========
    
    def get_customer(self, customer_id: int) -> dict[str, Any]:
        """Get single customer details (requires authentication).
        
        Args:
            customer_id: Customer ID
            
        Returns:
            Customer object
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/customers/{customer_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get customer {customer_id}: {e}")
    
    def list_customers(self, page: int = 1, search: str = "") -> dict[str, Any]:
        """List customers with pagination.
        
        Args:
            page: Page number (default: 1)
            search: Search query (default: "")
            
        Returns:
            Paginated response with 'count', 'next', 'previous', 'results'
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/customers",
                params={"page": page, "q": search},
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to list customers: {e}")
    
    def create_customer(
        self,
        email: str,
        first_name: str,
        last_name: str,
        **kwargs: Any
    ) -> int:
        """Create a new customer/student.
        
        Args:
            email: Customer email address
            first_name: Customer first name
            last_name: Customer last name
            **kwargs: Additional customer fields
            
        Returns:
            Created customer ID
            
        Raises:
            MarvelousAPIError: If creation fails
        """
        payload = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        }
        
        # Override with any additional kwargs
        payload.update(kwargs)
        
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/api/teachers/me/create_student",
                headers=self._get_auth_headers(),
                json=payload,
                timeout=15
            )
            resp.raise_for_status()
            customer = resp.json()
            return customer["id"]
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to create customer: {e}")
    
    def update_customer(self, customer_id: int, **updates: Any) -> dict[str, Any]:
        """Update an existing customer.
        
        Args:
            customer_id: Customer ID to update
            **updates: Fields to update (e.g., first_name="New Name")
            
        Returns:
            Updated customer object
            
        Raises:
            MarvelousAPIError: If update fails
        """
        # Get current customer
        customer = self.get_customer(customer_id)
        
        # Apply updates
        customer.update(updates)
        
        try:
            resp = self.session.put(
                f"{self.BASE_URL}/api/customers/{customer_id}",
                headers=self._get_auth_headers(),
                json=customer,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to update customer {customer_id}: {e}")
    
    def delete_customer(self, customer_id: int) -> None:
        """Delete a customer.
        
        Args:
            customer_id: Customer ID to delete
            
        Raises:
            MarvelousAPIError: If deletion fails
        """
        try:
            resp = self.session.delete(
                f"{self.BASE_URL}/api/customers/{customer_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to delete customer {customer_id}: {e}")

    # ========== MEDIA LIBRARY ITEMS (PARTIAL) ==========
    # Note: Media items involve file uploads. Only confirmed operations included.
    # See TODO for complete implementation.
    
    def list_media(self, page: int = 1) -> dict[str, Any]:
        """List media library items with pagination.
        
        Args:
            page: Page number (default: 1)
            
        Returns:
            Paginated response with 'count', 'next', 'previous', 'results'
            Results contain 12 media items per page.
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/media",
                params={"page": page},
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to list media: {e}")
    
    def get_media(self, media_id: int) -> dict[str, Any]:
        """Get single media library item details (requires authentication).
        
        Args:
            media_id: Media ID
            
        Returns:
            Media object with nested file object
            
        Raises:
            MarvelousAPIError: If request fails
        """
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/api/media/{media_id}",
                headers=self._get_auth_headers(),
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to get media {media_id}: {e}")
    
    def update_media(self, media_id: int, **updates: Any) -> dict[str, Any]:
        """Update an existing media library item.
        
        Note: This method handles converting nested objects to IDs.
        File uploads and creation are not yet implemented.
        
        Args:
            media_id: Media ID to update
            **updates: Fields to update (e.g., title="New Title")
            
        Returns:
            Updated media object
            
        Raises:
            MarvelousAPIError: If update fails
        """
        # Get current media
        media = self.get_media(media_id)
        
        # Prepare for update (convert nested objects to IDs)
        media = self._prepare_media_for_update(media)
        
        # Apply updates
        media.update(updates)
        
        try:
            resp = self.session.put(
                f"{self.BASE_URL}/api/media/{media_id}",
                headers=self._get_auth_headers(),
                json=media,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise MarvelousAPIError(f"Failed to update media {media_id}: {e}")
    
    @staticmethod
    def _prepare_media_for_update(media: dict[str, Any]) -> dict[str, Any]:
        """Prepare a media object from GET for PUT request.
        
        Converts nested objects to IDs.
        """
        # Convert media file object to ID
        if isinstance(media.get('media'), dict):
            media['media'] = media['media']['id']
        
        # Convert instructor object to ID
        if isinstance(media.get('instructor'), dict):
            media['instructor'] = media['instructor']['id']
        
        # Convert options (products) to IDs
        if isinstance(media.get('options'), list):
            media['options'] = [
                o['id'] if isinstance(o, dict) else o 
                for o in media['options']
            ]
        
        return media
