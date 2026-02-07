# Getting Started - WhatsApp Proof of Concept

This guide will walk you through testing WhatsApp automation before building the full system.

---

## Why Test WhatsApp First?

WhatsApp automation is the **highest-risk component** because:
- Uses unofficial WhatsApp Web API (whatsapp-web.js)
- Could result in account restrictions or bans
- We need to validate it works reliably before building the rest

**Goal:** Send a test message to your WhatsApp group and verify it works without issues.

---

## Prerequisites

✅ Node.js 18+ installed  
✅ Access to WhatsApp account you want to use  
✅ Admin access to the target WhatsApp group  

---

## Step-by-Step Setup

### 1. Install Dependencies

```bash
cd ~/Repos/twy-announce-poster
npm install
```

**Expected output:** Dependencies installed (may show some deprecation warnings - ignore for now)

---

### 2. Create Configuration File

```bash
cp .env.example .env
```

The `.env` file is already set to **DRY_RUN=true** for safety. Keep it that way for now.

---

### 3. Authenticate WhatsApp

```bash
npm run auth
```

**What happens:**
1. A QR code will appear in your terminal
2. Open WhatsApp on your phone
3. Go to: **Settings > Linked Devices > Link a Device**
4. Scan the QR code from your terminal

**Expected output:**
```
✅ WhatsApp client is ready!
✅ Authentication successful!
   Session saved to: ./sessions/whatsapp
```

**Note:** Your authentication is saved locally in `./sessions/whatsapp`. You won't need to scan the QR code again unless you delete this directory.

---

### 4. Find Your Group ID

```bash
npm run list-groups
```

**What happens:**
- Lists all your WhatsApp groups with their IDs

**Example output:**
```
📋 Your WhatsApp Groups:

1. TWEEE Class Group
   ID: 123456789@g.us

2. Family Chat
   ID: 987654321@g.us

💡 Copy the ID of your target group to .env as WHATSAPP_GROUP_ID
```

**Action required:**
1. Find your target group in the list
2. Copy its ID (e.g., `123456789@g.us`)
3. Edit `.env` and set: `WHATSAPP_GROUP_ID=123456789@g.us`

---

### 5. Test Posting (Dry Run)

```bash
npm run test
```

**What happens:**
- Simulates posting without actually sending (because DRY_RUN=true)

**Expected output:**
```
🔍 DRY RUN MODE - Would send to group: 123456789@g.us

Message:
---
🤖 Test message from twy-announce-poster automation

If you see this, WhatsApp posting is working! ✅
---
```

**Verify:** Does the group ID look correct? Is the message what you want?

---

### 6. Test Posting (Real)

⚠️ **This will actually post to your WhatsApp group**

1. Edit `.env` and change: `DRY_RUN=false`
2. Run the test again:

```bash
npm run test
```

**Expected output:**
```
✅ WhatsApp client is ready!
📤 Sending test message to group: 123456789@g.us
✅ Message sent successfully!
```

**Verify in WhatsApp:**
- Open the group on your phone
- You should see the test message posted by your linked account

---

## Success Criteria

✅ Authentication works without errors  
✅ Can list all groups  
✅ Test message appears in the target group  
✅ No account warnings or restrictions from WhatsApp  

---

## What's Next?

If all tests pass, WhatsApp automation is viable! Next steps:

1. **Get Google Drive document** - Need to analyze format for parser
2. **Design message template** - What should the posts look like?
3. **Investigate Marvelous integration** - How to get class links?
4. **Build scheduler** - Daily automation

See [TASKS.md](TASKS.md) for detailed next steps.

---

## Troubleshooting

### QR Code Not Appearing

**Problem:** `npm run auth` hangs or shows errors

**Solutions:**
- Make sure Node.js 18+ is installed: `node --version`
- Try deleting old session: `rm -rf sessions/`
- Check if Chromium dependencies are missing (rare on Mac)

---

### Authentication Failed

**Problem:** `❌ Authentication failed` message

**Solutions:**
- Delete old session: `rm -rf sessions/`
- Run `npm run auth` again with fresh QR code
- Make sure you scan the code quickly (QR codes expire)

---

### Group ID Not Found

**Problem:** `npm run list-groups` doesn't show your group

**Solutions:**
- Make sure you're a member of the group on WhatsApp
- Try refreshing: close and reopen WhatsApp on phone, then run again
- Wait a few minutes for WhatsApp to sync

---

### Message Not Posting

**Problem:** `npm run test` succeeds but no message appears

**Solutions:**
- Verify group ID is correct: check `.env` file
- Make sure DRY_RUN=false in `.env`
- Check if you have permission to post in the group
- Try manually sending a message in the group first

---

### Account Restrictions

**Problem:** WhatsApp warns about suspicious activity

**Solutions:**
- This is the **main risk** of using unofficial API
- If this happens, **STOP** and reconsider approach
- Alternative: WhatsApp Business API (requires approval, paid)
- Alternative: Manual posting with better tools

**Document your findings** - Did you encounter any restrictions?

---

## Questions to Answer

During testing, try to answer these:

1. **Speed:** How long does it take to send a message?
2. **Reliability:** Does it work consistently, or intermittent failures?
3. **Rate limits:** Can you send multiple messages in succession?
4. **Account safety:** Any warnings from WhatsApp?
5. **Session persistence:** Does the session stay valid across restarts?

Document answers in STATUS.md for future reference.

---

## Cleanup (Optional)

If you want to start fresh:

```bash
# Remove authentication session
rm -rf sessions/

# Remove node_modules (to reinstall)
rm -rf node_modules/
npm install
```

---

## Support

If you run into issues:
1. Check the troubleshooting section above
2. Review [WARP.md](WARP.md) for project context
3. Check [STATUS.md](STATUS.md) for known issues
