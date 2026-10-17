SHELL := /bin/bash
COMPOSE := docker compose
# Môi trường triển khai của bộ deploy/ (3 tầng): local (máy dev) hoặc production (server thật).
# Mỗi stack đọc .env (secret, cổng, giá trị riêng của máy) rồi env/$(ENV).env nếu stack có file đó
# (cấu hình theo môi trường, ghi đè .env). Dùng: cd deploy/<stack> && $(call stack_compose,<stack>) ...
ENV ?= local
DEPLOY_ENVS := local production
stack_compose = docker compose$(if $(wildcard deploy/$(1)/.env), --env-file .env)$(if $(wildcard deploy/$(1)/env/$(ENV).env), --env-file env/$(ENV).env)
REDOCLY_IMAGE := redocly/cli:1.34.5
NGINX_LOG_DIR := /var/log/waf/nginx

.DEFAULT_GOAL := help
.PHONY: help init config up down restart ps logs clean smoke e2e test test-python test-java net build deploy-init deploy-env deploy-up deploy-down deploy-ps server-up server-down e2e-server obs-up obs-down demo demo-apache test-alert build-base \
        tail-logs rotate-logs mock-reload bus-topology bus-purge bus-peek geoip lint-contracts experiment loadtest record-demo

help: ## Liệt kê các lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

init: ## Tạo .env (mật khẩu ngẫu nhiên) và thư mục dữ liệu
	@./scripts/init-env.sh
	@mkdir -p data/geoip

config: init ## Kiểm tra cú pháp docker-compose.yml với profile hiện tại
	$(COMPOSE) config --quiet && echo "docker-compose.yml hợp lệ"

up: init build-base ## Khởi động hạ tầng + các profile trong COMPOSE_PROFILES
	$(COMPOSE) up -d --build --wait

build-base: ## Build image nền Python dùng chung cho các service
	@if [ -d services/base ]; then docker build -q -t waf/base:dev services/base >/dev/null && echo "waf/base:dev sẵn sàng"; fi

down: ## Dừng hệ thống (giữ dữ liệu)
	$(COMPOSE) down

restart: down up ## Khởi động lại

ps: ## Trạng thái container
	$(COMPOSE) ps -a

logs: ## Xem log: make logs [s=<service>]
	$(COMPOSE) logs -f --tail=100 $(s)

clean: ## Dừng và XOÁ toàn bộ volume (log, queue, database)
	$(COMPOSE) down -v --remove-orphans

smoke: ## Kiểm tra nhanh toàn bộ hạ tầng
	@./scripts/smoke-test.sh

e2e: ## Kiểm thử end-to-end toàn pipeline (cần chạy đủ service)
	@./scripts/e2e-test.sh

test: test-python test-java ## Chạy toàn bộ unit test (Python trong image nền, Java bằng Maven)

test-python: build-base ## Unit test các service Python
	@docker run --rm -v "$(CURDIR)/services:/app:ro" waf/base:dev sh -ec '\
	  cd /app && python -B -m common.test_bus; \
	  for t in /app/*/test_*.py; do d=$$(dirname $$t); [ "$$d" = /app/common ] && continue; echo "== $$t"; \
	    (cd $$d && PYTHONPATH=/app:$$d python -B $$t) || exit 1; done'

test-java: ## Unit test detector Java (Maven trong container)
	docker run --rm -v "$(CURDIR)/services/detector:/w" -v "$$HOME/.m2:/root/.m2" -w /w \
		maven:3.9.11-eclipse-temurin-17 mvn -q -B test

# ── Triển khai tách 3 stack (Loại 1 Sensor / Loại 2 Pipeline / Loại 3 Platform) ──
net: ## Tạo network dùng chung waf-net (một lần)
	@docker network inspect waf-net >/dev/null 2>&1 || docker network create waf-net

build: init build-base ## Build image nền + toàn bộ image service (waf/*:dev)
	$(COMPOSE) build

deploy-init: ## Sinh .env cho 3 stack với secret khớp nhau
	@./scripts/init-deploy-env.sh

deploy-env: ## Kiểm tra môi trường ENV (local | production) có file cấu hình
	@test -f deploy/platform/env/$(ENV).env || { \
	  echo "ENV=$(ENV) không hợp lệ; chọn: $(DEPLOY_ENVS)" >&2; exit 2; }
	@echo "Môi trường: $(ENV) (deploy/platform/env/$(ENV).env)"

deploy-up: deploy-env net build deploy-init ## Dựng cả 3 stack (platform -> pipeline -> sensor); ENV=local|production
	cd deploy/platform && $(call stack_compose,platform) up -d --wait
	cd deploy/pipeline && $(call stack_compose,pipeline) up -d --wait
	cd deploy/sensor && $(call stack_compose,sensor) up -d --wait
	@echo "Dashboard: http://localhost:$${GATEWAY_PORT:-8080}  ·  Web demo: http://localhost:8081"

deploy-down: deploy-env ## Dừng cả 3 stack (giữ dữ liệu)
	-cd deploy/sensor && $(call stack_compose,sensor) down
	-cd deploy/pipeline && $(call stack_compose,pipeline) down
	-cd deploy/platform && $(call stack_compose,platform) down

deploy-ps: deploy-env ## Trạng thái cả 3 stack
	@echo "== platform =="; cd deploy/platform && $(call stack_compose,platform) ps
	@echo "== pipeline =="; cd deploy/pipeline && $(call stack_compose,pipeline) ps
	@echo "== sensor =="; cd deploy/sensor && $(call stack_compose,sensor) ps

server-up: ## Server thật (Ubuntu): cấu hình Nginx của máy chủ + dựng toàn bộ Docker (ENV=production) bằng một lệnh
	@./scripts/server-up.sh

e2e-server: ## Kịch bản kiểm thử end-to-end trên server thật (ARGS="--via-localhost --with-scope --report FILE")
	@./scripts/e2e-server.sh $(ARGS)

server-down: ## Server thật: dừng mọi container (giữ dữ liệu và cấu hình Nginx)
	-@$(MAKE) --no-print-directory obs-down
	@$(MAKE) --no-print-directory deploy-down ENV=production

obs-up: net build deploy-init ## Dựng stack quan sát (Prometheus + Grafana + exporter)
	$(COMPOSE) --profile obs build metrics-exporter
	cd deploy/observability && docker compose up -d --wait
	@echo "Grafana: http://localhost:3000  ·  Prometheus: http://localhost:9090"

obs-down: ## Dừng stack quan sát
	-cd deploy/observability && docker compose down

demo: ## Gửi request thử (bình thường + tấn công mẫu) tới Nginx mục tiêu
	@set -a; . ./.env; set +a; ./scripts/attack-demo.sh

demo-apache: ## Gửi request thử tới Apache mục tiêu (cần profile apache)
	@set -a; . ./.env; set +a; \
	TARGET_URL="http://127.0.0.1:$${WEB_APACHE_PORT:-8082}" HOST_HEADER=blog.local ./scripts/attack-demo.sh

record-demo: ## Ghi video demo các kịch bản vào docs/report/demo-video: make record-demo [k="kb1 kb2 kb3"] (kb4 cần mật khẩu máy chủ)
	@./scripts/record-demo/run.sh $(k)

test-alert: ## Gửi cảnh báo MẪU qua SMTP (mailpit) + webhook để kiểm tra đường cảnh báo
	@./scripts/send-test-alert.sh

tail-logs: ## Theo dõi access log của Nginx mục tiêu
	$(COMPOSE) exec web-nginx sh -c 'tail -n 20 -F $(NGINX_LOG_DIR)/*.access.log'

rotate-logs: ## Giả lập log rotation của Nginx (để kiểm thử C1)
	$(COMPOSE) exec web-nginx sh -c 'cd $(NGINX_LOG_DIR) && for f in *.access.log; do mv "$$f" "$$f.1"; done && nginx -s reopen'
	@echo "Đã rotate: *.access.log -> *.access.log.1, nginx đang ghi vào file mới"

mock-reload: ## Nạp lại allowlist của mock Scope sau khi sửa
	$(COMPOSE) exec mock-api nginx -s reload

bus-topology: ## Tạo các topic Kafka còn thiếu (idempotent, infra/kafka/create-topics.sh)
	$(COMPOSE) run --rm kafka-init

bus-purge: ## Xoá rồi tạo lại mọi topic sự kiện (mất toàn bộ message, giữ rules.changed)
	@for t in log.raw.ingested log.normalized log.enriched attack.detected anomaly.scored \
	          alert.triggered stage.completed log.enriched.by-domain waf.deadletter; do \
		$(COMPOSE) exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:19092 \
			--delete --if-exists --topic "$$t" && echo "deleted $$t"; \
	done
	@sleep 3; $(COMPOSE) run --rm kafka-init >/dev/null && echo "đã tạo lại topic"

bus-peek: ## Xem tối đa 5 message từ đầu topic: make bus-peek t=alert.triggered
	$(COMPOSE) exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:19092 \
		--topic $(or $(t),log.enriched) --from-beginning --max-messages 5 --timeout-ms 5000 \
		--property print.key=true

experiment: ## Thực nghiệm A/B/C + mốc ModSecurity CRS trên hệ thống đang chạy (experiments/README.md)
	@./experiments/run.sh

loadtest: ## Kiểm thử tải: ghi dồn LINES dòng log (mặc định 20000), đo thông lượng từng khâu
	@python3 experiments/loadtest.py --lines $(or $(LINES),20000)

geoip: ## Tải GeoIP DB (DB-IP Lite) cho C3
	@./scripts/fetch-geoip.sh

lint-contracts: ## Lint OpenAPI và kiểm tra cú pháp các file JSON contract
	docker run --rm -v "$(CURDIR)/contracts:/spec:ro" $(REDOCLY_IMAGE) \
		lint --config /spec/redocly.yaml /spec/openapi/scope.yaml /spec/openapi/auth.yaml \
		/spec/openapi/rules.yaml /spec/openapi/pipelines.yaml /spec/openapi/alerting.yaml /spec/openapi/dashboard.yaml
	@jq empty contracts/events/*.json contracts/events/examples/*.json \
		&& echo "JSON hợp lệ"
