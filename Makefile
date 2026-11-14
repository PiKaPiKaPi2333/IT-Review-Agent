.PHONY: help install up down logs ingest ask check test lint fmt eval migrate revision clean

# Windows 上 psycopg 的异步模式不支持 uvicorn 默认的 ProactorEventLoop
LOOP_ARG := $(if $(filter Windows_NT,$(OS)),--loop app.infra.loop:selector_loop_factory,)

help:  ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## 安装 Python 依赖（MinerU 单独装，见 README）
	pip install -r requirements.txt

up:  ## 启动 postgres + milvus 等基础设施
	docker compose up -d postgres etcd minio milvus

up-all:  ## 启动全栈（含 api 容器）
	docker compose up -d --build

down:  ## 停止并移除容器
	docker compose down

logs:  ## 查看 api 日志
	docker compose logs -f api

dev:  ## 本地热重载运行 API
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 $(LOOP_ARG)

check:  ## 连通性自检（LLM / Embedding / Milvus / PG / MinerU）
	python -m app.cli check

ingest:  ## 文档入库：make ingest FILES="a.pdf b.pdf"
	python -m app.cli ingest $(FILES)

ask:  ## 提问：make ask Q="审批权限是多少"
	python -m app.cli ask "$(Q)"

eval:  ## 跑 RAGAS 评估：make eval DATASET=eval_datasets/sample.yaml
	python -m app.cli eval $(DATASET) --out eval_report.json

test:  ## 单元测试
	pytest -q

lint:  ## 静态检查（需安装 ruff/mypy）
	ruff check app tests
	mypy app --ignore-missing-imports

fmt:  ## 格式化
	ruff format app tests

migrate:  ## 应用数据库迁移
	alembic upgrade head

revision:  ## 生成迁移：make revision M="add xxx"
	alembic revision --autogenerate -m "$(M)"

clean:  ## 清理缓存
	rm -rf .pytest_cache .ruff_cache **/__pycache__
