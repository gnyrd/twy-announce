# twy-whatsapp-poster

**Automated WhatsApp group admin posting for TWEEE class announcements**

## Documentation

📋 **Start here for detailed information:**

- [**System Status**](STATUS.md) - Current state and health
- [**Current Tasks**](TASKS.md) - Work in progress (1-4 weeks)
- [**Roadmap**](FEATURES.md) - Future plans and ideas  
- [**Change History**](HISTORY.md) - Completed milestones
- [**Full Documentation**](WARP.md) - Complete project guide

---

## Quick Start

### 1. Install Dependencies

```bash
# Node.js dependencies (for WhatsApp)
npm install

# Python dependencies (coming soon)
# pip install -r requirements.txt
```

### 2. Configure

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your settings
# At minimum, you'll need to set WHATSAPP_GROUP_ID after authentication
```

### 3. Authenticate WhatsApp

```bash
# Start authentication (will show QR code)
npm run auth

# Scan the QR code with WhatsApp (Settings > Linked Devices)
# Session will be saved for future use
```

### 4. Test Posting

```bash
# First, keep DRY_RUN=true in .env for safety
npm run test

# Once you've verified it works, set DRY_RUN=false and test again
npm run test
```

---

## Current Status

🟡 **Development Phase** - WhatsApp proof of concept in progress

See [STATUS.md](STATUS.md) for detailed current state.

---

## Project Goals

1. ✅ **Automate class announcements** - No more manual posting every day
2. ✅ **Post day-before reminders** - Give people time to prepare
3. ✅ **Include class links** - Fetch from Marvelous platform automatically
4. ✅ **Configurable timing** - Mid-afternoon Mountain Time (configurable)

---

## How It Works (When Complete)

1. **Daily scheduler** runs at configured time
2. **Google Drive parser** checks for next day's events
3. **Marvelous integration** fetches class links
4. **Message generator** creates post from template
5. **WhatsApp automation** posts to group

---

## Components

| Component | Status | Description |
|-----------|--------|-------------|
| WhatsApp Automation | ⏳ **Testing** | Posting to WhatsApp groups |
| Google Drive Parser | 📋 Planned | Extract events from schedule document |
| Scheduler | 📋 Planned | Daily automation |
| Marvelous Integration | 📋 Planned | Fetch class links |

---

## Known Limitations

⚠️ **WhatsApp automation uses unofficial API** - May result in account restrictions. Testing to assess risk.

See [WARP.md](WARP.md) for full list of limitations and considerations.

---

## Contributing

This is a personal automation project. See [WARP.md](WARP.md) for development guidelines and documentation system.

---

## License

Private project for personal use.
