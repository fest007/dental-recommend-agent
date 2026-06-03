import os
import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.database import init_db
from routers import settings, products, purchases, profiles, recommendations, chat, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Configure LangSmith tracing via environment variables
    # LangGraph reads these automatically — no manual instrumentation needed
    try:
        from db.database import async_session
        from sqlalchemy import select
        from db.models import LlmConfig
        from services.llm_config_service import _decrypt_api_key

        async with async_session() as db:
            result = await db.execute(select(LlmConfig).where(LlmConfig.id == 1))
            row = result.scalar_one_or_none()
            if row and row.langsmith_enabled and row.langsmith_api_key:
                ls_key = _decrypt_api_key(row.langsmith_api_key)
                os.environ["LANGSMITH_API_KEY"] = ls_key
                os.environ["LANGSMITH_TRACING"] = "true"
                os.environ["LANGSMITH_PROJECT"] = row.langsmith_project or "dental-recommend-agent"
                import logging
                logging.getLogger(__name__).info(
                    "LangSmith tracing enabled: project=%s", row.langsmith_project
                )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to configure LangSmith tracing: %s", exc)

    from scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="牙科设备推荐Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(purchases.router, prefix="/api/purchases", tags=["purchases"])
app.include_router(profiles.router, prefix="/api/profiles", tags=["profiles"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["recommendations"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(scheduler.router, prefix="/api/scheduler", tags=["scheduler"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def find_available_port(host: str, preferred_port: int) -> int:
    """Find an available port, starting with the preferred port."""
    import socket

    def is_port_available(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
                return True
        except OSError:
            return False

    # Try preferred port first
    if is_port_available(preferred_port):
        return preferred_port

    # Try nearby ports
    for offset in range(1, 100):
        for port in [preferred_port + offset, preferred_port - offset]:
            if 1024 <= port <= 65535 and is_port_available(port):
                return port

    # Fallback to random available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def write_port_info(data_dir: str, host: str, port: int):
    """Write port info file for Electron to read."""
    import json
    port_info = {
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "pid": os.getpid()
    }
    port_file = os.path.join(data_dir, "port.json")
    with open(port_file, 'w', encoding='utf-8') as f:
        json.dump(port_info, f)
    print(f"[main] Port info written to: {port_file}")


if __name__ == "__main__":
    import shutil
    import uvicorn

    # 设置默认编码为 UTF-8（解决 Windows GBK 编码问题）
    import sys
    if sys.platform == 'win32':
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
        os.environ['PYTHONIOENCODING'] = 'utf-8'

    # 获取数据目录（从环境变量或默认当前目录）
    data_dir = os.environ.get("DENTAL_AGENT_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        config_dir = data_dir
    else:
        config_dir = os.path.dirname(__file__) if os.path.dirname(__file__) else "."

    config_path = os.path.join(config_dir, "config.yaml")
    example_path = os.path.join(os.path.dirname(__file__) if os.path.dirname(__file__) else ".", "config.yaml.example")

    # 如果 config.yaml 不存在，从 example 复制或创建默认配置
    if not os.path.exists(config_path):
        if os.path.exists(example_path):
            with open(example_path, 'r', encoding='utf-8') as src:
                content = src.read()
            with open(config_path, 'w', encoding='utf-8') as dst:
                dst.write(content)
            print(f"[init] Created config from example: {config_path}")
        else:
            default_config = """llm:
  base_url: "https://api.openai.com/v1"
  api_key: ""
  ranking_model: "gpt-4o"
  enrichment_model: "gpt-4o-mini"
  embedding_model: "text-embedding-3-small"
  temperature: 0.7
  max_tokens: 4096
  timeout: 30

server:
  host: "127.0.0.1"
  port: 8765

database:
  path: "app.db"

qdrant:
  path: "qdrant"
  collection: "products"
"""
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(default_config)
            print(f"[init] Created default config: {config_path}")

    # 使用 UTF-8 编码读取配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    is_dev = os.environ.get("DENTAL_AGENT_DEV", "").lower() in ("1", "true", "yes")

    # 默认使用 127.0.0.1（只监听本地）
    host = config.get("server", {}).get("host", "127.0.0.1")
    preferred_port = config.get("server", {}).get("port", 8765)

    # 自动寻找可用端口
    port = find_available_port(host, preferred_port)
    if port != preferred_port:
        print(f"[main] Port {preferred_port} is busy, using port {port} instead")

    # 写入端口信息文件供 Electron 读取
    write_port_info(config_dir, host, port)

    print(f"[main] Starting server on {host}:{port}")
    print(f"[main] Data directory: {config_dir}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=is_dev,
    )
