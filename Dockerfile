FROM node:18-slim

# Install Chromium dependencies for Puppeteer (used by whatsapp-web.js)
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-ipafont-gothic fonts-wqy-zenhei fonts-thai-tlwg fonts-kacst fonts-freefont-ttf \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set Puppeteer to use installed Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Create app directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy application code
COPY whatsapp_bot.js list_groups.js ./

# Create directories for persistent data
RUN mkdir -p /app/sessions /app/credentials /app/logs

# Expose volumes for persistent data
VOLUME ["/app/sessions", "/app/credentials", "/app/logs"]

# Default command (can be overridden)
CMD ["node", "whatsapp_bot.js"]
