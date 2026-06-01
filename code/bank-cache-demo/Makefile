.PHONY: up down bench logs clean

up:
	docker compose up -d --build

down:
	docker compose down

clean:
	docker compose down -v

logs:
	docker compose logs -f backend

bench:
	docker compose --profile benchmark run --rm k6 run /scripts/benchmark.js
