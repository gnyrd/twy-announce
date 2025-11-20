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

**Choose your approach:**
- 🐳 **Docker** (recommended for deployment) → See [DOCKER.md](DOCKER.md)
- 💻 **Local** (faster for testing) → See [GETTING_STARTED.md](GETTING_STARTED.md)

### Docker Quick Start

```bash
# 1. Build
make build

# 2. Configure
cp .env.example .env
# Edit .env with your settings

# 3. Authenticate
make auth

# 4. Find group ID
make list-groups
# Add group ID to .env

# 5. Test
make test
```

### Local Quick Start

```bash
# 1. Install
npm install

# 2. Configure
cp .env.example .env

# 3. Authenticate
npm run auth

# 4. Find group ID
npm run list-groups

# 5. Test
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
