# Cross-Casting — Docker shortcuts
# Usage: make <target>     (run `make` or `make help` to list targets)

.DEFAULT_GOAL := help
.PHONY: help up build logs down restart ps health

help:      ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  make %-8s %s\n", $$1, $$2}'

up:        ## Start all services (uses local images only)
	docker compose up -d --pull never

build:     ## Rebuild the API image and start all services
	docker compose up -d --build --pull never

logs:      ## Follow API logs (Ctrl+C to exit)
	docker compose logs -f app

down:      ## Stop and remove all containers (data is kept)
	docker compose down

restart:   ## Restart the API (after changing code or .env)
	docker compose up -d --pull never --force-recreate app

ps:        ## Show container status
	docker compose ps

health:    ## Check API health
	@curl -sS http://localhost:8000/health && echo
