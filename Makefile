SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help init build-base up down restart ps logs demo peek-enriched tail-logs clean test

help: ## Liệt kê lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

init: ## Tạo .env (mật khẩu ngẫu nhiên) + thư mục dữ liệu
	@./scripts/init-env.sh
	@mkdir -p data/geoip

build-base: ## Build image nền Python dùng chung
	docker build -q -t waf/base:dev services/base >/dev/null && echo "waf/base:dev sẵn sàng"

up: init build-base ## Dựng web mục tiêu + RabbitMQ + mock scope + C1/C2/C3
	$(COMPOSE) up -d --build --wait
	@echo "Web: http://localhost:8081 · RabbitMQ UI: http://localhost:15672"

down: ## Dừng (giữ dữ liệu)
	$(COMPOSE) down

restart: down up ## Khởi động lại

ps: ## Trạng thái container
	$(COMPOSE) ps -a

logs: ## Xem log: make logs [s=<service>]
	$(COMPOSE) logs -f --tail=100 $(s)

demo: ## Gửi request thử (bình thường + tấn công) tới web mục tiêu -> sinh log
	@set -a; . ./.env; set +a; ./scripts/attack-demo.sh

peek-enriched: ## Xem vài bản ghi log.enriched trên bus (không tiêu thụ mất)
	@set -a; . ./.env; set +a; \
	curl -s -u "$$RABBITMQ_USER:$$RABBITMQ_PASSWORD" -H 'content-type: application/json' \
	  -X POST "http://127.0.0.1:$${RABBITMQ_UI_PORT:-15672}/api/queues/%2F/q.enriched/get" \
	  -d '{"count":5,"ackmode":"ack_requeue_true","encoding":"auto"}' \
	| jq -c '.[].payload | fromjson | {event,domain,method:.data.method,path:.data.path,status:.data.status,src_ip:.data.src_ip,req_count_1m:.data.req_count_1m,geo:.data.geo}'

tail-logs: ## Theo dõi access log của Nginx mục tiêu
	$(COMPOSE) exec web-nginx sh -c 'tail -n 20 -F /var/log/waf/nginx/*.access.log'

test: ## Chạy unit test C1/C2/C3 trong image nền
	@docker run --rm -e PYTHONPATH=/app -v "$(CURDIR)/services:/app:ro" waf/base:dev sh -c '\
	  python /app/c2-parser/test_parser.py && python /app/c3-enrichment/test_enrich.py && python /app/c1-ingestion/test_tailer.py'

clean: ## Dừng và XOÁ volume (log, queue)
	$(COMPOSE) down -v --remove-orphans
