# 星智股 StockNova · 多阶段构建（前端构建 → 后端运行时托管）
# 单容器部署：FastAPI 同时提供 API/WebSocket 与前端静态页面（同源、单端口 8000）。
# 行情/业务数据库通过 /data 卷持久化，重启不丢数据。

# ---------- Stage 1：构建前端 ----------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
# 用 pnpm（仓库锁定 pnpm-lock.yaml），corepack 免全局安装
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# ---------- Stage 2：后端运行时 ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STOCKNOVA_HOST=0.0.0.0 \
    STOCKNOVA_DATA_DIR=/data \
    STOCKNOVA_STATIC_DIR=/app/frontend/dist
WORKDIR /app/backend

# 用 uv 按 lock 文件还原依赖（--no-dev 跳过测试/检查工具，--frozen 严格锁定）
RUN pip install uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --no-dev --frozen

# 拷贝后端源码 + 前端构建产物
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# 数据卷（SQLite + DuckDB）持久化
VOLUME ["/data"]
EXPOSE 8000

# 单进程运行：DuckDB 单写者 + APScheduler + 内存态报价轮询，不可多 worker
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
