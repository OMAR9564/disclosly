# One-command entry points. See README.md.

COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.dev.yml
COMPOSE_FILES := infra/docker-compose.yml infra/docker-compose.dev.yml

.PHONY: up down down-v logs web lint fmt typecheck test check-compose check-web

check-compose:
	@for f in $(COMPOSE_FILES); do \
		test -f $$f || { echo "error: $$f does not exist yet (tasks.md T014)" >&2; exit 1; }; \
	done

check-web:
	@test -f web/package.json || { echo "error: web/package.json does not exist yet (tasks.md T003)" >&2; exit 1; }

READY_URL := http://localhost:8000/readyz
READY_TIMEOUT_S := 120

up: check-compose
	$(COMPOSE) --profile build build sandbox-image
	$(COMPOSE) up -d --build --wait
	@echo "waiting for $(READY_URL) (up to $(READY_TIMEOUT_S)s)"
	@deadline=$$(( $$(date +%s) + $(READY_TIMEOUT_S) )); \
	until curl -fsS --max-time 3 $(READY_URL) > /dev/null 2>&1; do \
		if [ $$(date +%s) -ge $$deadline ]; then \
			echo "error: $(READY_URL) did not return 200 within $(READY_TIMEOUT_S)s; run 'make logs'" >&2; \
			exit 1; \
		fi; \
		sleep 2; \
	done; \
	echo "ready: $(READY_URL)"

# Stops and removes the containers; keeps the volumes (database data).
down: check-compose
	$(COMPOSE) down

# Also removes the volumes.
down-v: check-compose
	$(COMPOSE) down -v

logs: check-compose
	$(COMPOSE) logs -f

web: check-web
	NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm --prefix web run dev

lint: check-web
	uv run ruff check .
	uv run ruff format --check .
	npm --prefix web run lint
	npm --prefix web run format:check

fmt: check-web
	uv run ruff check --fix .
	uv run ruff format .
	npm --prefix web run format

typecheck: check-web
	uv run mypy
	npm --prefix web run typecheck

test: check-web
	uv run pytest
	npm --prefix web test
