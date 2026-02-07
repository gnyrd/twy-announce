#!/usr/bin/env node

/**
 * WhatsApp Bot for TWEEE Group Admin Automation
 * 
 * This script handles WhatsApp Web authentication and message posting.
 * 
 * Usage:
 *   node whatsapp_bot.js auth          - Authenticate WhatsApp session
 *   node whatsapp_bot.js test          - Send test message to configured group
 *   node whatsapp_bot.js post <msg>    - Post message to configured group
 */

require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

// Configuration
const SESSION_PATH = process.env.WHATSAPP_SESSION_PATH || './sessions/whatsapp';
const GROUP_ID = process.env.WHATSAPP_GROUP_ID;
const DRY_RUN = process.env.DRY_RUN === 'true';

// Initialize WhatsApp client
const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'twy-poster',
        dataPath: SESSION_PATH
    }),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

// Event: QR code for authentication
client.on('qr', (qr) => {
    console.log('📱 Scan this QR code with WhatsApp:');
    qrcode.generate(qr, { small: true });
    console.log('\nOpen WhatsApp > Settings > Linked Devices > Link a Device');
});

// Event: Client ready
client.on('ready', async () => {
    console.log('✅ WhatsApp client is ready!');
    
    const command = process.argv[2];
    const message = process.argv.slice(3).join(' ');
    
    try {
        if (command === 'auth') {
            console.log('✅ Authentication successful!');
            console.log('   Session saved to:', SESSION_PATH);
            await client.destroy();
            process.exit(0);
        } else if (command === 'test') {
            await sendTestMessage();
        } else if (command === 'post' && message) {
            await postMessage(message);
        } else {
            console.log('Usage:');
            console.log('  node whatsapp_bot.js auth          - Authenticate');
            console.log('  node whatsapp_bot.js test          - Send test message');
            console.log('  node whatsapp_bot.js post <msg>    - Post message');
            await client.destroy();
            process.exit(1);
        }
    } catch (error) {
        console.error('❌ Error:', error.message);
        await client.destroy();
        process.exit(1);
    }
});

// Event: Authentication failure
client.on('auth_failure', (msg) => {
    console.error('❌ Authentication failed:', msg);
    process.exit(1);
});

// Event: Disconnected
client.on('disconnected', (reason) => {
    console.log('⚠️  Disconnected:', reason);
});

/**
 * Send a test message to verify posting works
 */
async function sendTestMessage() {
    if (!GROUP_ID) {
        console.error('❌ WHATSAPP_GROUP_ID not set in .env');
        console.log('\nTo find your group ID:');
        console.log('1. Run: node whatsapp_bot.js list-groups');
        console.log('2. Find your group in the list');
        console.log('3. Copy the ID to .env as WHATSAPP_GROUP_ID');
        await client.destroy();
        process.exit(1);
    }
    
    const testMessage = '🤖 Test message from twy-announce-poster automation\n\nIf you see this, WhatsApp posting is working! ✅';
    
    if (DRY_RUN) {
        console.log('🔍 DRY RUN MODE - Would send to group:', GROUP_ID);
        console.log('\nMessage:');
        console.log('---');
        console.log(testMessage);
        console.log('---');
    } else {
        console.log('📤 Sending test message to group:', GROUP_ID);
        const chat = await client.getChatById(GROUP_ID);
        await chat.sendMessage(testMessage);
        console.log('✅ Message sent successfully!');
    }
    
    await client.destroy();
    process.exit(0);
}

/**
 * Post a message to the configured group
 */
async function postMessage(message) {
    if (!GROUP_ID) {
        console.error('❌ WHATSAPP_GROUP_ID not set in .env');
        await client.destroy();
        process.exit(1);
    }
    
    if (DRY_RUN) {
        console.log('🔍 DRY RUN MODE - Would send to group:', GROUP_ID);
        console.log('\nMessage:');
        console.log('---');
        console.log(message);
        console.log('---');
    } else {
        console.log('📤 Posting message to group:', GROUP_ID);
        const chat = await client.getChatById(GROUP_ID);
        await chat.sendMessage(message);
        console.log('✅ Message posted successfully!');
    }
    
    await client.destroy();
    process.exit(0);
}

// Start the client
console.log('🚀 Starting WhatsApp client...');
client.initialize();
