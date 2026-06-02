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


if __name__ == "__main__":
    import shutil
    import uvicorn

    config_dir = os.path.dirname(__file__) if os.path.dirname(__file__) else "."
    config_path = os.path.join(config_dir, "config.yaml")
    example_path = os.path.join(config_dir, "config.yaml.example")

    # 如果 config.yaml 不存在，从 example 复制
    if not os.path.exists(config_path) and os.path.exists(example_path):
        shutil.copy2(example_path, config_path)
        print(f"[init] 已从 config.yaml.example 创建 config.yaml")

    with open(config_path) as f:
        config = yaml.safe_load(f)
    is_dev = os.environ.get("DENTAL_AGENT_DEV", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "main:app",
        host=config["server"]["host"],
        port=config["server"]["port"],
        reload=is_dev,
    )
