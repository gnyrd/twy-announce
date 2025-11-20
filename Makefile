.PHONY: help build up down logs shell auth list-groups test post clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build Docker image
	docker-compose build

up: ## Start container in background
	docker-compose up -d

down: ## Stop and remove container
	docker-compose down

logs: ## Show container logs
	docker-compose logs -f

shell: ## Open shell in container
	docker-compose run --rm whatsapp-poster /bin/bash

auth: ## Authenticate WhatsApp (scan QR code)
	docker-compose run --rm whatsapp-poster node whatsapp_bot.js auth

list-groups: ## List all WhatsApp groups
	docker-compose run --rm whatsapp-poster node list_groups.js

test: ## Send test message to configured group
	docker-compose run --rm whatsapp-poster node whatsapp_bot.js test

post: ## Post custom message (usage: make post MSG="your message")
	docker-compose run --rm whatsapp-poster node whatsapp_bot.js post "$(MSG)"

clean: ## Remove container, volumes, and built images
	docker-compose down -v
	docker rmi twy-whatsapp-poster_whatsapp-poster 2>/dev/null || true

# Local development (non-Docker)
.PHONY: install dev-auth dev-list dev-test

install: ## Install dependencies locally
	npm install

dev-auth: ## Local: Authenticate WhatsApp
	npm run auth

dev-list: ## Local: List groups
	npm run list-groups

dev-test: ## Local: Test posting
	npm run test
