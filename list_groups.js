#!/usr/bin/env node

/**
 * List all WhatsApp groups to find the group ID
 * 
 * Usage:
 *   node list_groups.js
 */

require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const SESSION_PATH = process.env.WHATSAPP_SESSION_PATH || './sessions/whatsapp';

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

client.on('qr', (qr) => {
    console.log('📱 Scan this QR code with WhatsApp:');
    qrcode.generate(qr, { small: true });
    console.log('\nOpen WhatsApp > Settings > Linked Devices > Link a Device');
});

client.on('ready', async () => {
    console.log('✅ WhatsApp client is ready!');
    console.log('\n📋 Your WhatsApp Groups:\n');
    
    try {
        const chats = await client.getChats();
        const groups = chats.filter(chat => chat.isGroup);
        
        if (groups.length === 0) {
            console.log('No groups found.');
        } else {
            groups.forEach((group, index) => {
                console.log(`${index + 1}. ${group.name}`);
                console.log(`   ID: ${group.id._serialized}`);
                console.log('');
            });
            
            console.log('\n💡 Copy the ID of your target group to .env as WHATSAPP_GROUP_ID');
        }
        
        await client.destroy();
        process.exit(0);
    } catch (error) {
        console.error('❌ Error:', error.message);
        await client.destroy();
        process.exit(1);
    }
});

client.on('auth_failure', (msg) => {
    console.error('❌ Authentication failed:', msg);
    process.exit(1);
});

console.log('🚀 Starting WhatsApp client...');
client.initialize();
