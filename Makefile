.PHONY: help env ssl venv test test-quick lint \
       build build-bot build-fetcher \
       up up-bot up-fetcher down down-bot down-fetcher \
       logs logs-fetcher restart \
       clean clean-docker clean-all

COMPOSE_BOT   = docker compose -f docker-compose-bot.yaml
COMPOSE_FETCH = docker compose -f docker-compose-fetcher.yaml
VENV          = .venv/bin/python
GIT_COMMIT   := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BASE_VERSION := v2.0.0

SAMPLE_ENVS = bot.sample.env postgres.sample.env rabbit.sample.env fetcher.sample.env

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

env: ## Copy *.sample.env to *.env (skip existing)
	@for f in $(SAMPLE_ENVS); do \
		target=$${f/.sample.env/.env}; \
		if [ -f "$$target" ]; then \
			echo "  $$target already exists, skipping"; \
		else \
			cp "$$f" "$$target"; \
			echo "  created $$target from $$f — review and edit as needed"; \
		fi; \
	done

ssl: ssl/ca.crt ## Generate self-signed certs for local RabbitMQ TLS

ssl/ca.crt:
	@echo "==> Generating self-signed CA + server + client certificates in ssl/"
	@mkdir -p ssl
	openssl genrsa -out ssl/ca.key 4096
	openssl req -x509 -new -nodes -key ssl/ca.key -sha256 -days 3650 \
		-out ssl/ca.crt -subj "/CN=RabbitMQ-CA"
	openssl genrsa -out ssl/server.key 4096
	openssl req -new -key ssl/server.key -out ssl/server.csr -subj "/CN=rabbitmq"
	openssl x509 -req -in ssl/server.csr -CA ssl/ca.crt -CAkey ssl/ca.key \
		-CAcreateserial -out ssl/server.crt -days 3650 -sha256
	openssl genrsa -out ssl/client.key 4096
	openssl req -new -key ssl/client.key -out ssl/client.csr -subj "/CN=fetcher"
	openssl x509 -req -in ssl/client.csr -CA ssl/ca.crt -CAkey ssl/ca.key \
		-CAcreateserial -out ssl/client.crt -days 3650 -sha256
	@rm -f ssl/*.csr ssl/*.srl
	@echo "==> Certificates generated in ssl/"

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

venv: .venv/.installed ## Create/update .venv with project + test dependencies

.venv/.installed: requirements-bot.txt
	@if [ ! -d .venv ]; then \
		echo "==> Creating virtual environment"; \
		python3 -m venv .venv; \
	fi
	.venv/bin/pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet -r requirements-bot.txt pytest pytest-asyncio pytest-mock ruff
	@touch $@

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: venv ## Run full test suite (verbose)
	PYTHONPATH=src $(VENV) -m pytest -vvv src/tests/

test-quick: venv ## Run test suite (summary only)
	PYTHONPATH=src $(VENV) -m pytest src/tests/

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: venv ## Lint source code with ruff
	.venv/bin/ruff check src/

# ---------------------------------------------------------------------------
# Docker — build
# ---------------------------------------------------------------------------

build: build-bot build-fetcher ## Build all Docker images

build-bot: ## Build bot image
	$(COMPOSE_BOT) build --build-arg GIT_COMMIT=$(GIT_COMMIT) --build-arg BASE_VERSION=$(BASE_VERSION)

build-fetcher: ## Build fetcher image
	$(COMPOSE_FETCH) build --build-arg GIT_COMMIT=$(GIT_COMMIT) --build-arg BASE_VERSION=$(BASE_VERSION)

# ---------------------------------------------------------------------------
# Docker — run
# ---------------------------------------------------------------------------

up: env ssl ## Start all services (postgres + rabbitmq + bot + fetcher)
	$(COMPOSE_BOT) up -d --build
	$(COMPOSE_FETCH) up -d --build

up-bot: env ssl ## Start bot stack only (postgres + rabbitmq + bot)
	$(COMPOSE_BOT) up -d --build

up-fetcher: ## Start fetcher only
	$(COMPOSE_FETCH) up -d --build

down: ## Stop all services
	$(COMPOSE_BOT) down
	$(COMPOSE_FETCH) down

down-bot: ## Stop bot stack only
	$(COMPOSE_BOT) down

down-fetcher: ## Stop fetcher only
	$(COMPOSE_FETCH) down

logs: ## Tail bot stack logs
	$(COMPOSE_BOT) logs -f

logs-fetcher: ## Tail fetcher logs
	$(COMPOSE_FETCH) logs -f

restart: down up ## Restart bot stack

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove Python caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

clean-docker: ## Remove Docker volumes
	$(COMPOSE_BOT) down -v
	$(COMPOSE_FETCH) down -v

clean-all: clean clean-docker ## Remove caches + Docker volumes
