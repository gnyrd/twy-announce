# HeyMarvelous (Namastream) API Documentation

**Last Updated:** 2026-01-20

This document describes the undocumented API endpoints discovered for the HeyMarvelous/Namastream platform.

## Base URL

```
https://api.namastream.com
```

## Authentication

### Login Flow

The authentication process is a two-step flow:

#### Step 1: Email/Password Login

```http
POST /auth/login/
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password"
}
```

**Response:** 200 OK (triggers magic code email)

#### Step 2: Magic Code Verification

```http
POST /auth/magic-code/
Content-Type: application/json

{
  "magic_code": "123456"
}
```

**Response:**
```json
{
  "key": "e03a92b48f2bf1f54835c70447b03791bbf8ad84",
  "user_type": "teacher"
}
```

### Using the Token

All authenticated requests must include the token in the Authorization header:

```http
Authorization: Token e03a92b48f2bf1f54835c70447b03791bbf8ad84
```

## Event Operations

### List Events (Public)

```http
GET /api/studios/{studio-slug}/events
```

**Auth Required:** No

**Example:**
```
GET /api/studios/tiffany-wood-yoga/events
```

**Response:** Array of event objects (trimmed version)

### Get Single Event

```http
GET /api/events/{event_id}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Full event object with all fields

### Create Event

```http
POST /api/events
Authorization: Token {your-token}
Content-Type: application/json
```

**Required Fields:**
- `event_name` - String
- `event_type` - String (e.g., "collaborative_group_event")
- `event_start_datetime` - ISO 8601 datetime
- `event_end_datetime` - ISO 8601 datetime
- `date` - Date string (YYYY-MM-DD)
- `start_time` - Time string (HH:MM)
- `duration_hours` - Integer
- `duration_minutes` - Integer
- `instructors` - Array of instructor IDs
- `products` - Array of product IDs

**Minimal Example:**
```json
{
  "event_name": "Test Class",
  "event_type": "collaborative_group_event",
  "event_start_datetime": "2026-01-25T16:00:00.000Z",
  "event_end_datetime": "2026-01-25T17:00:00.000Z",
  "date": "2026-01-25",
  "start_time": "09:00",
  "duration_hours": 1,
  "duration_minutes": 0,
  "instructors": [7553],
  "products": [50855],
  "registration_required": false,
  "post_event_recording_available": true,
  "recording_expiration_days": 3,
  "registration_limit": 0,
  "registration_closes_minutes_before_start": 0,
  "unregistration_closes_minutes_before_start": 0,
  "days_of_week": [],
  "agreed_to_www_requirements": false,
  "is_repeating_event": false,
  "is_free_event": false,
  "require_first_name": false,
  "require_last_name": false,
  "hide_registration_info": false,
  "email_notifications_enabled": true,
  "sms_notifications_enabled": true,
  "is_punchable_event": true,
  "attachments": [],
  "auto_notify_waitlist": true,
  "copy_event_description_to_recording": true,
  "student": null
}
```

**Response:** 201 Created with full event object including generated `id`

### Update Event

```http
PUT /api/events/{event_id}
Authorization: Token {your-token}
Content-Type: application/json
```

**Important:** 
- Must send the complete event object
- When updating, nested objects (products, instructors) must be converted to ID arrays
- The GET response returns full objects, but PUT expects IDs only

**Example:**
```python
# Get event
event = get_event(event_id)

# Fix nested objects before updating
event['products'] = [p['id'] if isinstance(p, dict) else p for p in event['products']]
event['instructors'] = [i['id'] if isinstance(i, dict) else i for i in event['instructors']]

# Update field
event['event_name'] = "Updated Name"

# Send update
put_event(event_id, event)
```

**Response:** 200 OK with updated event object

### Delete Event

```http
DELETE /api/events/{event_id}
Authorization: Token {your-token}
```

**Response:** 204 No Content

## Event Description Format

Event descriptions use EditorJS JSON format in the `event_description_new` field.

### Basic Structure

```json
{
  "time": 1737371202000,
  "blocks": [
    {
      "id": "unique-block-id",
      "type": "paragraph",
      "data": {
        "text": "Your text content here"
      },
      "tunes": {
        "alignmentTuneTool": {
          "alignment": "left"
        }
      }
    }
  ],
  "version": "2.26.5"
}
```

### Supported Block Types

#### Paragraph
```json
{
  "id": "para-1",
  "type": "paragraph",
  "data": {
    "text": "Plain text or <b>HTML formatted</b> text"
  },
  "tunes": {
    "alignmentTuneTool": {
      "alignment": "left"
    }
  }
}
```

#### Header
```json
{
  "id": "header-1",
  "type": "header",
  "data": {
    "text": "Header Text",
    "level": 3
  },
  "tunes": {
    "alignmentTuneTool": {
      "alignment": "left"
    }
  }
}
```

### Notes
- Single paragraph blocks work fine
- Multiple blocks (headers + paragraphs) also work
- HTML formatting supported: `<b>`, `<i>`, etc.
- Each block needs a unique `id`

## Common Patterns

### Converting Event Objects for Update

```python
def prepare_event_for_update(event):
    """Prepare an event object from GET for PUT request."""
    # Convert nested objects to IDs
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
```

### Creating Simple Description

```python
import json

def create_simple_description(text):
    """Create a simple single-paragraph description."""
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
```

## Error Responses

### 400 Bad Request
```json
{
  "products": ["Incorrect type. Expected pk value, received dict."],
  "instructors": ["Incorrect type. Expected pk value, received dict."]
}
```
**Solution:** Convert nested objects to ID arrays

### 401 Unauthorized
```json
{
  "detail": "Authentication credentials were not provided."
}
```
**Solution:** Include `Authorization: Token {key}` header

### 404 Not Found
Event doesn't exist or has been deleted.

## Known Limitations

1. **No batch operations** - Must create/update/delete events one at a time
2. **Token expiration** - Unknown; may need periodic re-authentication
3. **Rate limiting** - Unknown; untested
4. **Instructor/Product IDs** - Must be obtained from existing events or other endpoints

## Discovery Notes

- Public event listing endpoint doesn't require auth
- Individual event details require authentication
- All write operations (POST/PUT/DELETE) require authentication
- HAR file captures may not show Authorization headers due to browser security
- The API uses Token-based authentication (not JWT or OAuth)

## Product Operations

### Get Product

```http
GET /api/products/{product_id}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Full product object

### Create Product

```http
POST /api/products
Authorization: Token {your-token}
Content-Type: application/json
```

**Minimal Example:**
```json
{
  "product_name": "My New Product",
  "product_type": "media_library"
}
```

**Common Product Types:**
- `media_library` - Media library/membership
- Additional types may exist (undocumented)

**Response:** 201 Created with product object including generated `id`

### Update Product

```http
PUT /api/products/{product_id}
Authorization: Token {your-token}
Content-Type: application/json
```

**Important:**
- Must send the complete product object
- When updating, nested `tags` array must contain only tag IDs (not full objects)
- The GET response returns full tag objects, but PUT expects IDs only

**Example:**
```python
# Get product
product = get_product(product_id)

# Fix nested objects before updating
product['tags'] = [t['id'] if isinstance(t, dict) else t for t in product['tags']]

# Update field
product['product_name'] = "Updated Product Name"

# Send update
put_product(product_id, product)
```

**Response:** 200 OK with updated product object

### Delete Product

```http
DELETE /api/products/{product_id}
Authorization: Token {your-token}
```

**Response:** 204 No Content

### List Product Tags

```http
GET /api/product-tags
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Array of tag objects

## Product Fields

Common product fields include:

- `id` - Product ID (read-only)
- `product_name` - Product name/title
- `product_type` - Product type (e.g., "media_library")
- `pricing_type` - Pricing model (e.g., "free", "paid")
- `published` - Boolean, whether product is published
- `visible` - Boolean, whether product is visible
- `archived` - Boolean, whether product is archived
- `tags` - Array of tag IDs
- `cover_file` - Cover image file ID
- `thumbnail_file` - Thumbnail file ID
- `recurring_options` - Complex pricing structure object
- `content_count` - Number of content items
- `content_limit` - Maximum content items allowed
- Many more fields...


## Coupon Operations

### Get Coupon

```http
GET /api/coupons-paginated/{coupon_id}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Full coupon object

### List Coupons

```http
GET /api/coupons-paginated?page={page}&q={search}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Parameters:**
- `page` - Page number (10 results per page)
- `q` - Search query (optional)

**Response:** Paginated response
```json
{
  "count": 71,
  "next": "https://api.namastream.com/api/coupons-paginated?page=2&q=",
  "previous": null,
  "results": [...]
}
```

### Get Coupon Stats

```http
GET /api/coupons-paginated/stats
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:**
```json
{
  "total_coupons": 1866,
  "used": 64,
  "total_revenue": 16546.83
}
```

### Create Coupon

```http
POST /api/coupons-paginated
Authorization: Token {your-token}
Content-Type: application/json
```

**Required Fields:**
- `code` - Coupon code (string)
- `name` - Coupon name/description (string)
- `discount_amount` - Discount amount as string (e.g., "50")
- `discount_type` - "percentage" or "fixed"

**Example:**
```json
{
  "code": "SUMMER50",
  "name": "Summer Sale 50% Off",
  "discount_amount": "50",
  "discount_type": "percentage",
  "duration_type": "unlimited",
  "duration_units": "1",
  "max_redemptions": "100",
  "redeem_end": "2026-08-31",
  "redeem_start": "2026-06-01",
  "products": [87290]
}
```

**Response:** 201 Created with coupon object including generated `id`

### Update Coupon

```http
PUT /api/coupons-paginated/{coupon_id}
Authorization: Token {your-token}
Content-Type: application/json
```

**Important:**
- Must send the complete coupon object
- When updating, nested `products` array must contain only product IDs (not full objects)
- The GET response returns full product objects, but PUT expects IDs only

**Response:** 200 OK with updated coupon object

### Delete Coupon

```http
DELETE /api/coupons-paginated/{coupon_id}
Authorization: Token {your-token}
```

**Response:** 204 No Content

## Coupon Fields

Common coupon fields include:

- `id` - Coupon ID (read-only)
- `code` - Coupon code
- `name` - Coupon name/description
- `discount_amount` - Discount amount (as string)
- `discount_type` - "percentage" or "fixed"
- `duration_type` - "unlimited" or other duration types
- `duration_units` - Number of duration units
- `max_redemptions` - Maximum number of uses
- `used` - Number of times used (read-only)
- `is_active` - Boolean, whether coupon is active
- `redeem_start` - Start date (YYYY-MM-DD)
- `redeem_end` - End date (YYYY-MM-DD)
- `products` - Array of product IDs


## Customer Operations

### Get Customer

```http
GET /api/customers/{customer_id}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Full customer object

### List Customers

```http
GET /api/customers?page={page}&q={search}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Parameters:**
- `page` - Page number (10 results per page)
- `q` - Search query (optional)

**Response:** Paginated response with 'count', 'next', 'previous', 'results'

### Create Customer

```http
POST /api/teachers/me/create_student
Authorization: Token {your-token}
Content-Type: application/json
```

**Required Fields:**
- `email` - Customer email address
- `first_name` - Customer first name
- `last_name` - Customer last name

**Example:**
```json
{
  "email": "customer@example.com",
  "first_name": "John",
  "last_name": "Doe"
}
```

**Response:** 200 OK with customer object including generated `id`

**Note:** Creation uses a teacher-specific endpoint, but all other operations use the standard `/customers` endpoints.

### Update Customer

```http
PUT /api/customers/{customer_id}
Authorization: Token {your-token}
Content-Type: application/json
```

**Important:**
- Must send the complete customer object
- Unlike events/products/coupons, no nested object conversion needed

**Response:** 200 OK with updated customer object

### Delete Customer

```http
DELETE /api/customers/{customer_id}
Authorization: Token {your-token}
```

**Response:** 200 OK (note: returns 200, not 204)

## Customer Fields

Common customer fields include:

- `id` - Customer ID (read-only)
- `first_name` - First name
- `last_name` - Last name
- `full_name` - Full name (computed, read-only)
- `email` - Email address
- `phone` - Phone number
- `address` - Street address
- `city` - City
- `zipcode` - ZIP/postal code
- `state` - State/province
- `country` - Country
- `currency` - Currency code
- `status` - Customer status
- `signed_waiver` - Boolean, whether waiver is signed
- `total_spend` - Total amount spent (read-only)
- `last_time_purchase` - Last purchase date (read-only)
- Various notification preferences (email/SMS)


## Media Library Item Operations

**Note:** Media library items involve file uploads for creation. Video files are hosted on Vimeo and not directly downloadable via API.

### List Media Items

```http
GET /api/media?page={page}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Parameters:**
- `page` - Page number (optional, default: 1)

**Response:** Paginated response with 12 results per page
```json
{
  "count": 1285,
  "next": "https://api.namastream.com/api/media?page=2",
  "previous": null,
  "results": [...]
}
```

**Note:** Media library items are more complex as they involve file uploads. This documentation reflects confirmed endpoints only. Further investigation needed for complete implementation.

### Get Media Item

```http
GET /api/media/{media_id}
Authorization: Token {your-token}
```

**Auth Required:** Yes

**Response:** Full media object including nested file object

### Create Media Item

```http
POST /api/media
Authorization: Token {your-token}
Content-Type: application/json
```

**Minimal Example:**
```json
{
  "media": 1674922
}
```

**Notes:**
- Requires a file to be uploaded first (see upload endpoints)
- `media` field contains the uploaded file ID
- Returns 201 Created with media object including generated `id`

### Update Media Item

```http
PUT /api/media/{media_id}
Authorization: Token {your-token}
Content-Type: application/json
```

**Example:**
```json
{
  "id": 865414,
  "title": "My Video",
  "description_new": "{...EditorJS JSON...}",
  "media": 1674922,
  "instructor": 7553,
  "options": [45611],
  "release_on": "2026-01-22T00:02:00.000Z",
  "prevent_forward_seeking": false
}
```

**Important:**
- Must send complete media object
- `options` field contains product IDs and likely needs ID conversion
- Uses EditorJS format for `description_new`

### File Upload Helper

```http
GET /api/files/upload_video
Authorization: Token {your-token}
```

**Purpose:** Likely returns upload URL/parameters for video uploads

**Auth Required:** Yes

## Media Library Item Fields

Common fields include:

- `id` - Media ID (read-only)
- `title` - Media title
- `description_new` - Description in EditorJS format
- `media` - File ID (reference to uploaded file)
- `instructor` - Instructor ID
- `options` - Array of product IDs (which products include this media)
- `release_on` - Scheduled release date/time
- `prevent_forward_seeking` - Boolean, prevent video scrubbing
- `likes`, `views` - Engagement metrics (read-only)
- `position` - Sort position
- `is_new` - Boolean, marked as new content
- `attachments` - Array of attachment IDs
- `included_in_products` - Read-only list
- `included_in_lessons` - Read-only list
- `included_in_playlists` - Read-only list

### File Object Structure

The `media` field when retrieved contains a nested file object:
- `id` - File ID
- `file_type` - e.g., "vimeo"
- `file_content` - e.g., "video"
- `video_url` - Video path
- `uploaded` - Upload timestamp
- `owner` - Owner object with id and full_name
- And many more fields...

## Known Limitations

1. **File upload process not fully documented** - Need to investigate upload flow
2. **DELETE endpoint not confirmed** - Likely exists at `/api/media/{id}` but untested
3. **List endpoint unknown** - No pagination/listing endpoint found yet
4. **Complex workflow** - Requires multi-step process (upload file, create media item)

