// Copyright (c) 2026 Ninsim, Inc. All rights reserved.

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

/**
 * Minimal CLI to send a message to a WhatsApp group by its exact name.
 *
 * Usage:
 *   node src/send_to_whatsapp.js "Group Name" "Message text here"
 *
 * On first run, this will print a QR code in the terminal – scan it with the
 * WhatsApp account that owns The Yoga Lifestyle community. The session will be
 * persisted under .wwebjs_auth so subsequent runs do not require re‑auth.
 */

async function main() {
  const [groupName, ...messageParts] = process.argv.slice(2);

  if (!groupName || messageParts.length === 0) {
    console.error('Usage: node src/send_to_whatsapp.js "Group Name" "Message text"');
    process.exit(1);
  }

  const messageText = messageParts.join(' ');

  const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });

  client.on('qr', (qr) => {
    console.log('Scan this QR code with WhatsApp to authenticate:');
    qrcode.generate(qr, { small: true });
  });

  client.on('ready', async () => {
    console.log('WhatsApp client is ready. Looking for group:', groupName);

    try {
      const chats = await client.getChats();
      const group = chats.find((c) => c.isGroup && c.name === groupName);

      if (!group) {
        console.error(`Group with name "${groupName}" not found.`);
        await client.destroy();
        process.exit(1);
      }

      console.log(`Sending message to group "${group.name}"...`);
      await client.sendMessage(group.id._serialized, messageText);
      console.log('Message sent successfully.');
    } catch (err) {
      console.error('Error sending message:', err);
      process.exitCode = 1;
    } finally {
      // Give WhatsApp-web.js a brief moment to flush the send before exiting.
      setTimeout(async () => {
        await client.destroy();
        process.exit();
      }, 2000);
    }
  });

  client.on('auth_failure', (msg) => {
    console.error('Authentication failure:', msg);
  });

  client.initialize();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
