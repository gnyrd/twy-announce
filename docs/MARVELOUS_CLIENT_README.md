# MarvelousClient - Python Library for HeyMarvelous API

A Python client library for interacting with the HeyMarvelous (Namastream) API to manage yoga studio events.

## Installation

No installation needed - the library is self-contained. Just import it:

```python
from src.marvelous_client import MarvelousClient
```

## Quick Start

```python
from marvelous_client import MarvelousClient

# Initialize with token
client = MarvelousClient(auth_token="your-token-here")

# List events (public endpoint)
events = client.list_events("tiffany-wood-yoga")

# Get single event (requires auth)
event = client.get_event(event_id=123456)

# Create event
event_id = client.create_event(
    event_name="New Yoga Class",
    event_start_datetime="2026-01-25T16:00:00.000Z",
    event_end_datetime="2026-01-25T17:00:00.000Z",
    date="2026-01-25",
    start_time="09:00",
    duration_hours=1,
    duration_minutes=0,
    instructors=[7553],
    products=[50855],
    event_description="A relaxing yoga session"
)

# Update event
client.update_event(event_id, event_name="Updated Class Name")

# Delete event
client.delete_event(event_id)
```

## Authentication

Get your auth token by logging into the HeyMarvelous admin interface and capturing the network traffic, or use the authenticate method (requires email access for magic code):

```python
client = MarvelousClient()
auth_data = client.authenticate(
    email="your@email.com",
    password="your-password",
    magic_code="123456"  # From email
)
print(f"Token: {auth_data['key']}")
```

## API Reference

### MarvelousClient

#### `__init__(auth_token=None)`
Initialize the client with an optional auth token.

#### `list_events(studio_slug: str) -> list[dict]`
List all events for a studio. Public endpoint, no authentication required.

```python
events = client.list_events("tiffany-wood-yoga")
```

#### `get_event(event_id: int) -> dict`
Get full details for a single event. Requires authentication.

```python
event = client.get_event(123456)
```

#### `create_event(...) -> int`
Create a new event. Returns the created event ID.

**Required Parameters:**
- `event_name`: str
- `event_start_datetime`: str (ISO 8601)
- `event_end_datetime`: str (ISO 8601)
- `date`: str (YYYY-MM-DD)
- `start_time`: str (HH:MM)
- `duration_hours`: int
- `duration_minutes`: int
- `instructors`: list[int]
- `products`: list[int]

**Optional Parameters:**
- `event_description`: str (plain text, converted to EditorJS format)
- `event_type`: str (default: "collaborative_group_event")
- Any other event field as kwargs

#### `update_event(event_id: int, **updates) -> dict`
Update an existing event. Pass field names as kwargs.

```python
client.update_event(123456, event_name="New Name", duration_hours=2)
```

#### `delete_event(event_id: int) -> None`
Delete an event.

### Helper Functions

#### `create_rich_description(blocks: list[dict]) -> str`
Create a rich EditorJS description with custom blocks.

```python
blocks = [
    {
        "id": "header-1",
        "type": "header",
        "data": {"text": "Class Title", "level": 3},
        "tunes": {"alignmentTuneTool": {"alignment": "left"}}
    },
    {
        "id": "para-1",
        "type": "paragraph",
        "data": {"text": "Class description with <b>bold</b> text"},
        "tunes": {"alignmentTuneTool": {"alignment": "left"}}
    }
]

description = MarvelousClient.create_rich_description(blocks)
client.create_event(..., event_description_new=description)
```

## Examples

See `examples/marvelous_example.py` for complete usage examples.

Run the example:
```bash
export MARVELOUS_TOKEN="your-token-here"
python3 examples/marvelous_example.py
```

## Error Handling

The library raises two exception types:

- `MarvelousAPIError`: General API errors
- `MarvelousAuthError`: Authentication-specific errors

```python
from marvelous_client import MarvelousClient, MarvelousAPIError, MarvelousAuthError

try:
    client = MarvelousClient(auth_token="invalid-token")
    event = client.get_event(123456)
except MarvelousAuthError as e:
    print(f"Auth error: {e}")
except MarvelousAPIError as e:
    print(f"API error: {e}")
```

## Notes

- The public event listing endpoint doesn't require authentication
- All other operations (get, create, update, delete) require authentication
- When updating events, the library automatically handles converting nested objects (products, instructors) to ID arrays
- Event descriptions use EditorJS JSON format internally, but you can pass plain text to `create_event()` and it will be converted automatically

## Full Documentation

See `docs/MARVELOUS_API.md` for complete API documentation including:
- Authentication flow details
- All available event fields
- EditorJS description format
- Error responses
- Known limitations

## License

Part of the twy-whatsapp-poster project.

## Product Management

The library also supports full CRUD operations for products.

### Basic Usage

```python
# Get product
product = client.get_product(product_id=12345)

# Create product
product_id = client.create_product(
    product_name="My Product",
    product_type="media_library"
)

# Update product
client.update_product(product_id, product_name="Updated Name")

# Delete product
client.delete_product(product_id)

# List available tags
tags = client.list_product_tags()
```

### Product API Methods

#### `get_product(product_id: int) -> dict`
Get full details for a single product. Requires authentication.

```python
product = client.get_product(12345)
```

#### `create_product(product_name: str, product_type: str = "media_library", **kwargs) -> int`
Create a new product. Returns the created product ID.

**Parameters:**
- `product_name`: str - Product name/title
- `product_type`: str - Product type (default: "media_library")
- `**kwargs`: Additional product fields

```python
product_id = client.create_product(
    product_name="Yoga Membership",
    product_type="media_library",
    pricing_type="free"
)
```

#### `update_product(product_id: int, **updates) -> dict`
Update an existing product. Pass field names as kwargs.

The library automatically handles converting nested tag objects to IDs.

```python
client.update_product(12345, product_name="New Name", visible=True)
```

#### `delete_product(product_id: int) -> None`
Delete a product.

```python
client.delete_product(12345)
```

#### `list_product_tags() -> list[dict]`
List all available product tags.

```python
tags = client.list_product_tags()
for tag in tags:
    print(f"{tag['id']}: {tag['name']}")
```

### Product Types

Common product types:
- `media_library` - Media library/membership product

### Notes on Products

- Like events, the GET response returns full tag objects, but PUT expects only tag IDs
- The library automatically handles this conversion in `update_product()`
- Minimal product creation requires only `product_name` and `product_type`
- Many additional fields available for pricing, visibility, content limits, etc.


## Coupon Management

The library supports full CRUD operations for coupons, plus listing and stats.

### Basic Usage

```python
# Get coupon
coupon = client.get_coupon(coupon_id=12345)

# List coupons (paginated)
result = client.list_coupons(page=1, search="SUMMER")
print(f"Total: {result['count']}, Results: {len(result['results'])}")

# Get stats
stats = client.get_coupon_stats()
print(f"Total: {stats['total_coupons']}, Revenue: ${stats['total_revenue']}")

# Create coupon
coupon_id = client.create_coupon(
    code="SUMMER50",
    name="Summer Sale",
    discount_amount="50",
    discount_type="percentage",
    products=[87290],
    redeem_start="2026-06-01",
    redeem_end="2026-08-31",
    max_redemptions="100"
)

# Update coupon
client.update_coupon(coupon_id, discount_amount="60")

# Delete coupon
client.delete_coupon(coupon_id)
```

### Coupon API Methods

#### `get_coupon(coupon_id: int) -> dict`
Get full details for a single coupon.

```python
coupon = client.get_coupon(12345)
```

#### `list_coupons(page: int = 1, search: str = "") -> dict`
List coupons with pagination. Returns dict with 'count', 'next', 'previous', 'results'.

```python
result = client.list_coupons(page=1, search="SALE")
for coupon in result['results']:
    print(f"{coupon['code']}: {coupon['discount_amount']}% off")
```

#### `get_coupon_stats() -> dict`
Get coupon statistics. Returns dict with 'total_coupons', 'used', 'total_revenue'.

```python
stats = client.get_coupon_stats()
print(f"Revenue: ${stats['total_revenue']}")
```

#### `create_coupon(...) -> int`
Create a new coupon. Returns the created coupon ID.

**Required Parameters:**
- `code`: str - Coupon code
- `name`: str - Coupon name/description
- `discount_amount`: str - Discount amount (e.g., "50" for 50%)
- `discount_type`: str - "percentage" or "fixed" (default: "percentage")

**Optional Parameters:**
- `products`: list[int] - Product IDs
- `redeem_start`: str - Start date (YYYY-MM-DD)
- `redeem_end`: str - End date (YYYY-MM-DD)
- `max_redemptions`: str - Max uses
- `duration_type`: str - "unlimited" (default)
- `duration_units`: str - Duration units
- `**kwargs`: Additional fields

```python
coupon_id = client.create_coupon(
    code="WELCOME10",
    name="Welcome 10% Off",
    discount_amount="10",
    products=[12345, 67890],
    max_redemptions="50"
)
```

#### `update_coupon(coupon_id: int, **updates) -> dict`
Update an existing coupon. Pass field names as kwargs.

The library automatically handles converting nested product objects to IDs.

```python
client.update_coupon(12345, 
    name="Updated Name",
    discount_amount="75"
)
```

#### `delete_coupon(coupon_id: int) -> None`
Delete a coupon.

```python
client.delete_coupon(12345)
```

### Coupon Discount Types

- `percentage` - Percentage discount (e.g., "50" for 50% off)
- `fixed` - Fixed amount discount

### Notes on Coupons

- Like events and products, GET returns full product objects but PUT expects IDs
- The library automatically handles this conversion in `update_coupon()`
- Coupons are paginated (10 per page)
- Discount amounts should be passed as strings
- Dates should be in YYYY-MM-DD format


## Customer Management

The library supports full CRUD operations for customers/students.

### Basic Usage

```python
# Get customer
customer = client.get_customer(customer_id=12345)

# List customers (paginated)
result = client.list_customers(page=1, search="john")
print(f"Total: {result['count']}, Results: {len(result['results'])}")

# Create customer
customer_id = client.create_customer(
    email="john.doe@example.com",
    first_name="John",
    last_name="Doe"
)

# Update customer
client.update_customer(customer_id, phone="555-1234")

# Delete customer
client.delete_customer(customer_id)
```

### Customer API Methods

#### `get_customer(customer_id: int) -> dict`
Get full details for a single customer.

```python
customer = client.get_customer(12345)
print(f"{customer['full_name']}: ${customer['total_spend']}")
```

#### `list_customers(page: int = 1, search: str = "") -> dict`
List customers with pagination. Returns dict with 'count', 'next', 'previous', 'results'.

```python
result = client.list_customers(page=1, search="smith")
for customer in result['results']:
    print(f"{customer['email']}: {customer['status']}")
```

#### `create_customer(email: str, first_name: str, last_name: str, **kwargs) -> int`
Create a new customer/student. Returns the created customer ID.

**Required Parameters:**
- `email`: str - Customer email address
- `first_name`: str - Customer first name
- `last_name`: str - Customer last name

**Optional Parameters:**
- `phone`: str - Phone number
- `address`, `city`, `state`, `zipcode`, `country`: Address fields
- `**kwargs`: Additional customer fields

```python
customer_id = client.create_customer(
    email="jane@example.com",
    first_name="Jane",
    last_name="Smith",
    phone="555-5678",
    city="Denver"
)
```

#### `update_customer(customer_id: int, **updates) -> dict`
Update an existing customer. Pass field names as kwargs.

Unlike events/products/coupons, customer updates don't require any nested object conversions.

```python
client.update_customer(12345, 
    phone="555-9999",
    city="Boulder"
)
```

#### `delete_customer(customer_id: int) -> None`
Delete a customer.

```python
client.delete_customer(12345)
```

### Notes on Customers

- Creation uses a teacher-specific endpoint: `/api/teachers/me/create_student`
- All other operations use standard REST endpoints: `/api/customers/{id}`
- No nested object conversions needed (simpler than events/products/coupons!)
- Customers are paginated (10 per page)
- DELETE returns 200 (not 204 like other resources)
- Customers may also be referred to as "students" in some API contexts


## Media Library Items (Partial Support)

**Note:** Media library item management is partially implemented. Only read and update operations are currently supported. File uploads and creation require further investigation.

### Available Operations

```python
# List media items (paginated, 12 per page)
result = client.list_media(page=1)
print(f"Total: {result['count']}, Page: {len(result['results'])}")
for item in result['results']:
    print(f"{item['id']}: {item['title']}")

# Get media item
media = client.get_media(media_id=12345)
print(f"Title: {media['title']}")
print(f"Views: {media['views']}")

# Update media item
client.update_media(12345, title="Updated Video Title")
```

### Not Yet Implemented

- `create_media()` - Requires file upload workflow
- `delete_media()` - Endpoint exists but untested

- File upload functionality

### Notes

- Media items reference uploaded files by ID
- The library automatically handles converting nested objects (media file, instructor, products) to IDs
- Creating media items requires a two-step process: 1) Upload file, 2) Create media item linking to file
- **Videos are Vimeo-hosted** and not directly downloadable via API
- Media files are for streaming/playback only, not downloading
- Vimeo video IDs are accessible in the media file object
- See `docs/MARVELOUS_API.md` for more details on media library items

