"""
Chat router — ReAct Agent with LangGraph interrupt-based human-in-the-loop.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一个牙科设备推荐专家，服务于B2B牙科设备营销场景。
你可以查询用户画像、购买记录、商品信息、推荐结果，也可以执行添加购买记录、删除记录、修改画像、生成推荐等操作。
使用中文回答，语气专业、友好。"""


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息")
    thread_id: Optional[str] = Field(None, description="对话线程ID，首次不传")


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    action: Optional[dict] = None  # interrupt payload (pending tool call)


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool = Field(..., description="true=确认执行, false=取消")


@router.post("/", summary="Send a chat message")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    """Run the ReAct agent. Returns response + interrupt info if paused for confirmation."""
    from services import llm_config_service
    from services.chat_graph import run_chat_graph, new_thread_id

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()
    thread_id = body.thread_id or new_thread_id()

    result = await run_chat_graph(
        thread_id=thread_id,
        user_message=body.message,
        system_prompt=_SYSTEM_PROMPT,
        db=db, llm_client=client, llm_config=config,
    )

    return ChatResponse(
        response=result["response"],
        thread_id=thread_id,
        action=result.get("action"),
    )


@router.post("/stream", summary="Send a chat message with streaming response")
async def chat_stream(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Run the ReAct agent with SSE streaming response."""
    from services import llm_config_service
    from services.chat_graph import run_chat_graph_stream, new_thread_id

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()
    thread_id = body.thread_id or new_thread_id()

    async def event_generator():
        try:
            async for chunk in run_chat_graph_stream(
                thread_id=thread_id,
                user_message=body.message,
                system_prompt=_SYSTEM_PROMPT,
                db=db, llm_client=client, llm_config=config,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/resume", summary="Resume after confirmation")
async def resume(body: ResumeRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    """Resume a paused agent graph after user confirms or rejects a write tool."""
    from services import llm_config_service
    from services.chat_graph import resume_chat_graph

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()

    result = await resume_chat_graph(
        thread_id=body.thread_id,
        approved=body.approved,
        db=db, llm_client=client, llm_config=config,
    )

    return ChatResponse(
        response=result["response"],
        thread_id=body.thread_id,
        action=result.get("action"),
    )


@router.post("/resume-stream", summary="Resume after confirmation with streaming")
async def resume_stream(body: ResumeRequest, db: AsyncSession = Depends(get_db)):
    """Resume a paused agent graph with SSE streaming response."""
    from services import llm_config_service
    from services.chat_graph import resume_chat_graph_stream

    client = await llm_config_service.get_client()
    config = await llm_config_service.get_config()

    async def event_generator():
        try:
            async for chunk in resume_chat_graph_stream(
                thread_id=body.thread_id,
                approved=body.approved,
                db=db, llm_client=client, llm_config=config,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
