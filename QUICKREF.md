# Quick Reference


```bash
# First time setup
cp .env.example .env               # Create config (edit after)
make auth                          # Authenticate WhatsApp (scan QR)
make list-groups                   # Find your group ID
# Edit .env: add WHATSAPP_GROUP_ID

# Testing
make test                          # Send test message (respects DRY_RUN)

# Deployment
make up                            # Start in background
make logs                          # View logs
make down                          # Stop

# Utilities
make shell                         # Open bash in container
make post MSG="Hello!"            # Post custom message
make clean                         # Remove everything
make help                          # Show all commands
```

---

## Local Commands (Faster for Development)

```bash
# First time setup
npm install
cp .env.example .env
npm run auth
npm run list-groups
# Edit .env: add WHATSAPP_GROUP_ID

# Testing
npm run test

# Custom posting
node whatsapp_bot.js post "Custom message"
```

---

## Configuration (.env)

```bash
# Required
WHATSAPP_GROUP_ID=123456789@g.us

# Safety (set to false when ready)
DRY_RUN=true

# Optional
POSTING_TIME=14:00
TIMEZONE=America/Denver
```

---

## File Structure

```
twy-announce-poster/
├── WARP.md              # Project overview & AI rules
├── STATUS.md            # Current state
├── TASKS.md             # Current work
├── FEATURES.md          # Roadmap
├── GETTING_STARTED.md   # Local setup guide
├── whatsapp_bot.js      # Main bot script
├── Makefile             # Convenient commands
└── .env                 # Your config (not in git)
```

---

## Troubleshooting

### Can't see QR code
```bash
npm run auth                       # Local
```

### Session lost
```bash
ls -la ./sessions/                 # Check it exists
make clean && make build && make auth  # Rebuild
```

### Wrong group
```bash
make list-groups                   # Find correct ID
# Edit .env
```

### Not posting
- Check `DRY_RUN=false` in `.env`
- Verify `WHATSAPP_GROUP_ID` is correct
- Check you can post manually in group

---

## Next Steps After WhatsApp Works

1. Share Google Drive document for parser design
2. Investigate Marvelous platform integration
3. Build message template system
4. Add scheduler for daily automation
5. Deploy to VPS or cloud

See [TASKS.md](TASKS.md) for detailed next steps.
