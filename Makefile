# One-command entry points. See README.md.

COMPOSE := docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml
COMPOSE_FILES := infra/docker-compose.yml infra/docker-compose.dev.yml

.PHONY: up down logs web lint fmt typecheck test check-compose check-web

check-compose:
	@for f in $(COMPOSE_FILES); do \
		test -f $$f || { echo "error: $$f does not exist yet (tasks.md T014)" >&2; exit 1; }; \
	done

check-web:
	@test -f web/package.json || { echo "error: web/package.json does not exist yet (tasks.md T003)" >&2; exit 1; }

up: check-compose
	$(COMPOSE) up -d --build --wait

down: check-compose
	$(COMPOSE) down

logs: check-compose
	$(COMPOSE) logs -f

web: check-web
	npm --prefix web run dev

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
