# MinerU 需要较重的系统依赖（PDF 渲染、OCR、字体）。
# 基础镜像用官方 Python slim + 显式安装依赖，避免隐式依赖导致解析失败。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        fonts-noto-cjk \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# MinerU 走官方 API（MINERU_API_KEY），容器内无需安装任何解析依赖，
# 因此不需要 torch / magic-pdf / OCR 相关系统库，镜像显著变小。
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN mkdir -p /data/uploads /data/parsed

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
