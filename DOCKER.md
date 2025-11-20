# Docker Setup Guide

This guide covers running the WhatsApp poster in Docker for development and deployment.

---

## Why Docker?

**Benefits:**
- ✅ Consistent environment (same on Mac, Linux, cloud)
- ✅ Easy deployment to cloud platforms
- ✅ Isolated from your system
- ✅ Persistent sessions via volumes

**Trade-offs:**
- ⚠️ Initial setup slightly more complex
- ⚠️ Uses more resources than native

---

## Prerequisites

- **Mac:** OrbStack (recommended) or Docker Desktop
- **Other platforms:** Docker Desktop
- Docker Compose (included with OrbStack/Docker Desktop)

**Check installation:**
```bash
docker --version
docker-compose --version
```

**Note for Mac users:** This project uses OrbStack instead of Docker Desktop. OrbStack is faster, lighter, and more Mac-native than Docker Desktop.

---

## Quick Start (Docker)

### 1. Build the Image

```bash
make build
```

Or without make:
```bash
docker-compose build
```

---

### 2. Set Up Configuration

```bash
cp .env.example .env
# Edit .env with your settings (keep DRY_RUN=true initially)
```

---

### 3. Authenticate WhatsApp

```bash
make auth
```

Or without make:
```bash
docker-compose run --rm whatsapp-poster node whatsapp_bot.js auth
```

**What happens:**
- Container starts with Chromium
- QR code appears in terminal
- Scan with WhatsApp (Settings > Linked Devices)
- Session saved to `./sessions/` (persisted on host)

---

### 4. Find Group ID

```bash
make list-groups
```

Or without make:
```bash
docker-compose run --rm whatsapp-poster node list_groups.js
```

Copy your group ID to `.env` as `WHATSAPP_GROUP_ID`

---

### 5. Test Posting

**Dry run first:**
```bash
make test
```

**Real posting:**
1. Edit `.env`: set `DRY_RUN=false`
2. Run: `make test`

---

## Common Commands

### Using Makefile (Recommended)

```bash
make help           # Show all available commands
make build          # Build Docker image
make auth           # Authenticate WhatsApp
make list-groups    # List WhatsApp groups
make test           # Send test message
make post MSG="Hi!" # Post custom message
make logs           # View container logs
make shell          # Open shell in container
make clean          # Remove everything
```

### Using Docker Compose Directly

```bash
# Build
docker-compose build

# Run commands
docker-compose run --rm whatsapp-poster node whatsapp_bot.js auth
docker-compose run --rm whatsapp-poster node list_groups.js
docker-compose run --rm whatsapp-poster node whatsapp_bot.js test

# Start in background (for scheduled posts)
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

---

## Local vs Docker Development

**You can use either approach:**

### Local (No Docker)
```bash
npm install
npm run auth
npm run list-groups
npm run test
```

**Pros:** Faster, simpler for testing  
**Cons:** Not portable, different from production

### Docker
```bash
make build
make auth
make list-groups
make test
```

**Pros:** Production-like, portable  
**Cons:** Slightly slower, more disk space

**Recommendation:** Test locally first, then verify in Docker before deploying.

---

## Persistent Data

Docker uses volumes to persist data between container restarts:

```
./sessions/      → WhatsApp authentication
./credentials/   → Google Drive API credentials (future)
./logs/          → Application logs (future)
```

These directories are mounted from your host machine, so data survives container restarts.

---

## Deployment Scenarios

### 1. Local Mac (Always-On)

**Using Docker Compose:**
```bash
make build
make auth
# Edit .env with group ID
make up  # Runs in background
make logs  # Check it's working
```

Container will restart automatically if Mac reboots.

---

### 2. Cloud Deployment (Future)

**Google Cloud Run:**
- Build: `docker build -t gcr.io/PROJECT_ID/whatsapp-poster .`
- Push: `docker push gcr.io/PROJECT_ID/whatsapp-poster`
- Deploy: Use Cloud Scheduler + Cloud Run

**AWS ECS:**
- Push image to ECR
- Create task definition
- Schedule with EventBridge

**Notes:**
- Need to handle WhatsApp session persistence (Cloud Storage, EFS, etc.)
- May need to re-authenticate periodically
- Consider serverless limitations (cold starts, Chromium size)

---

### 3. VPS (DigitalOcean, Linode, etc.)

**Most straightforward for this use case:**

```bash
# On VPS
git clone <repo>
cd twy-whatsapp-poster
cp .env.example .env
# Edit .env
make build
make auth
make up
```

Set up systemd or cron to ensure container stays running.

---

## Troubleshooting

### QR Code Not Visible

**Problem:** Can't see QR code in terminal

**Solution:** 
```bash
# Make sure container is running interactively
docker-compose run --rm whatsapp-poster node whatsapp_bot.js auth
```

---

### Session Lost After Restart

**Problem:** Have to re-authenticate after `docker-compose down`

**Cause:** Volume mapping issue

**Solution:**
```bash
# Check volume exists
ls -la ./sessions/

# Rebuild if needed
make clean
make build
make auth
```

---

### Container Won't Start

**Problem:** `docker-compose up` fails

**Solutions:**
```bash
# Check logs
docker-compose logs

# Rebuild
make build

# Try running directly
docker-compose run --rm whatsapp-poster /bin/bash
```

---

### Chromium Issues

**Problem:** Puppeteer/Chromium errors

**Solution:**
```bash
# Rebuild with fresh base image
docker-compose build --no-cache

# Check Chromium is installed
docker-compose run --rm whatsapp-poster chromium --version
```

---

## Resource Usage

**Expected usage:**
- **Disk:** ~1-1.5GB (image + Chromium)
- **Memory:** ~300-500MB while running
- **CPU:** Low when idle, spikes during posting

**To limit resources:**

Edit `docker-compose.yml`:
```yaml
services:
  whatsapp-poster:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

---

## Security Notes

### Secrets Management

**DO NOT commit:**
- `.env` file (contains secrets)
- `sessions/` directory (WhatsApp auth)
- `credentials/` directory (Google Drive API)

These are in `.gitignore` and `.dockerignore`.

**For production:** Use Docker secrets or environment injection:
```bash
docker run -e WHATSAPP_GROUP_ID=xyz ...
```

---

### Network Security

Container has no exposed ports by default (doesn't need them).

If you add a web interface later:
```yaml
ports:
  - "3000:3000"  # Only expose if needed
```

---

## Next Steps

After testing WhatsApp in Docker:

1. **Verify session persistence** - Stop/start container, ensure no re-auth needed
2. **Test scheduled running** - Keep container up for 24 hours
3. **Add Python scheduler** - Build multi-language image or separate service
4. **Plan cloud deployment** - Choose platform based on needs

See [FEATURES.md](FEATURES.md) for cloud deployment roadmap.

---

## Reference

**Docker Compose File Structure:**
```
docker-compose.yml     # Service definition
Dockerfile            # Image build instructions
.dockerignore         # Files to exclude from image
Makefile             # Convenience commands
```

**Key Docker Commands:**
- `docker-compose build` - Build image
- `docker-compose up` - Start services
- `docker-compose down` - Stop services
- `docker-compose run` - Run one-off command
- `docker-compose logs` - View logs
- `docker-compose exec` - Run command in running container

**Documentation:**
- [Docker Compose docs](https://docs.docker.com/compose/)
- [whatsapp-web.js Docker](https://github.com/pedroslopez/whatsapp-web.js/issues?q=docker)
