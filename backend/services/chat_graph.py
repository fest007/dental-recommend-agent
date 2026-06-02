"""
LangGraph ReAct Agent with native interrupt-based human-in-the-loop.

Architecture:
    - Agent has ALL tools (read + write)
    - Graph uses `interrupt_before=["tools"]` to pause before ANY tool execution
    - For read tools: auto-approve (resume immediately)
    - For write tools: return to frontend for user confirmation
    - User confirms → graph resumes → tool executes
    - Conversation history persisted by LangGraph checkpointer (thread_id)

The interrupt is a HARD constraint at the framework level — write tools
cannot execute without going through the interrupt/resume cycle.
"""

import contextvars
import json
import logging
import re
import uuid
from datetime import date as date_type
from typing import Any, Annotated, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request-scoped context (per-coroutine isolation via contextvars)
# ---------------------------------------------------------------------------

_chat_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "chat_ctx", default={"db": None, "llm_client": None, "llm_config": None}
)


def set_chat_context(db=None, llm_client=None, llm_config=None):
    """Set request-scoped context for the current async task.

    Uses contextvars so each concurrent request gets its own isolated copy.
    """
    current = _chat_ctx.get().copy()
    if db is not None:
        current["db"] = db
    if llm_client is not None:
        current["llm_client"] = llm_client
    if llm_config is not None:
        current["llm_config"] = llm_config
    _chat_ctx.set(current)


def _get_ctx() -> dict:
    """Get the current request's context."""
    return _chat_ctx.get()


# ---------------------------------------------------------------------------
# Read-only tools (auto-approved by interrupt handler)
# ---------------------------------------------------------------------------

@tool
async def query_user_profile(user_id: str) -> str:
    """查询用户画像信息。当用户询问某客户的画像、偏好、类型时调用。

    Args:
        user_id: 用户ID，如 KH3734
    """
    from sqlalchemy import select
    from db.models import UserProfile

    db = _get_ctx()["db"]
    stmt = select(UserProfile).where(UserProfile.user_id == user_id.upper())
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if not profile:
        return f"未找到用户 {user_id} 的画像"

    pj = profile.profile_json or {}
    basic = pj.get("basic_info", {})
    return json.dumps({
        "user_id": user_id.upper(),
        "customer_type": basic.get("customer_type", "未知"),
        "value_tier": pj.get("value_tier", "未知"),
        "purchase_span_days": basic.get("purchase_span_days", 0),
        "last_purchase": basic.get("last_purchase_date"),
        "category_preference": pj.get("category_preference", [])[:5],
        "brand_preference": pj.get("brand_preference", [])[:5],
        "recency_score": pj.get("recency_score", 0),
    }, ensure_ascii=False)


@tool
async def query_purchases(user_id: str, limit: int = 10) -> str:
    """查询用户的购买记录。

    Args:
        user_id: 用户ID，如 KH3734
        limit: 返回记录数量，默认10
    """
    from sqlalchemy import select
    from db.models import UserPurchase

    db = _get_ctx()["db"]
    stmt = (
        select(UserPurchase)
        .where(UserPurchase.user_id == user_id.upper())
        .order_by(UserPurchase.purchase_date.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return f"用户 {user_id} 没有购买记录"

    return json.dumps([
        {"id": r.id, "date": r.purchase_date.isoformat(), "sku": r.sku, "product_name": r.product_name, "quantity": r.quantity}
        for r in rows
    ], ensure_ascii=False)


@tool
async def query_product(sku: str) -> str:
    """查询商品信息（增强后的结构化数据）。

    Args:
        sku: 商品SKU编码，如 XH0027-1
    """
    from sqlalchemy import select
    from db.models import ProductEnriched

    db = _get_ctx()["db"]
    stmt = select(ProductEnriched).where(ProductEnriched.sku == sku.upper())
    result = await db.execute(stmt)
    prod = result.scalar_one_or_none()

    if not prod:
        return f"未找到商品 {sku}"

    return json.dumps({
        "sku": prod.sku, "name": prod.name, "brand": prod.brand,
        "category": f"{prod.category_l1} > {prod.category_l2}",
        "type": prod.product_type, "scenario": prod.usage_scenario,
        "keywords": prod.keywords or [],
    }, ensure_ascii=False)


@tool
async def query_recommendations(user_id: str) -> str:
    """查询用户的推荐结果。

    Args:
        user_id: 用户ID，如 KH3734
    """
    from sqlalchemy import select
    from db.models import Recommendation

    db = _get_ctx()["db"]
    stmt = select(Recommendation).where(Recommendation.user_id == user_id.upper()).order_by(Recommendation.rank)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return f"用户 {user_id} 暂无推荐结果"

    return json.dumps([
        {"id": r.id, "rank": r.rank, "sku": r.recommended_sku, "reason": r.reason, "confidence": r.confidence, "source": r.source, "status": r.status}
        for r in rows
    ], ensure_ascii=False)


# ---------------------------------------------------------------------------
# Write tools (paused by interrupt for user confirmation)
# ---------------------------------------------------------------------------

@tool
async def add_purchase(
    user_id: str,
    sku: str,
    quantity: int = 1,
    purchase_date: str = "",
    product_name: str = "",
) -> str:
    """添加购买记录。

    Args:
        user_id: 用户ID，如 KH3734
        sku: 商品SKU编码（新旧均可，自动标准化）
        quantity: 购买数量，默认1
        purchase_date: 购买日期，格式 YYYY-MM-DD，默认今天
        product_name: 商品名称，可选
    """
    from sqlalchemy import select
    from db.models import UserPurchase, ProductEnriched, SkuMapping
    from utils.sku_mapping import standardize_purchase

    db = _get_ctx()["db"]

    mapping_result = await db.execute(select(SkuMapping))
    sku_map = {m.old_sku: m.new_sku for m in mapping_result.scalars().all() if m.old_sku and m.new_sku}
    standardized = standardize_purchase({"sku": sku, "original_sku": ""}, sku_map)
    final_sku = standardized["sku"]
    original_sku = standardized.get("original_sku", "")

    if not product_name:
        stmt = select(ProductEnriched.name).where(ProductEnriched.sku == final_sku)
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        product_name = row if row else final_sku

    try:
        pdate = date_type.fromisoformat(purchase_date) if purchase_date else date_type.today()
    except ValueError:
        pdate = date_type.today()

    purchase = UserPurchase(
        user_id=user_id.upper(), sku=final_sku, product_name=product_name,
        quantity=quantity, purchase_date=pdate,
        original_sku=original_sku, import_batch="assistant",
    )
    db.add(purchase)
    await db.commit()

    # Refresh user profile (non-fatal)
    try:
        from services.user_profile import compute_profile
        await compute_profile(user_id.upper(), db)
    except Exception as exc:
        logger.warning("Profile refresh failed after add_purchase for %s: %s", user_id, exc)

    return json.dumps({"status": "success", "message": f"已添加: {user_id.upper()} | {final_sku} | {product_name} | x{quantity} | {pdate}"}, ensure_ascii=False)


@tool
async def delete_purchase(
    purchase_id: int = 0,
    user_id: str = "",
    sku: str = "",
    purchase_date: str = "",
) -> str:
    """删除购买记录。

    Args:
        purchase_id: 购买记录ID（最精确，优先使用）
        user_id: 用户ID（配合sku定位）
        sku: 商品SKU（配合user_id定位）
        purchase_date: 购买日期（可选，精确匹配）
    """
    from sqlalchemy import select, delete as sa_delete
    from db.models import UserPurchase

    db = _get_ctx()["db"]

    if purchase_id:
        # Check existence first
        result = await db.execute(select(UserPurchase).where(UserPurchase.id == purchase_id))
        row = result.scalar_one_or_none()
        if not row:
            return json.dumps({"status": "error", "message": f"未找到 ID={purchase_id} 的购买记录"})
        uid = row.user_id
        await db.delete(row)
        await db.commit()
        # Refresh profile (non-fatal)
        try:
            from services.user_profile import compute_profile
            await compute_profile(uid, db)
        except Exception as exc:
            logger.warning("Profile refresh failed after delete_purchase for %s: %s", uid, exc)
        return json.dumps({"status": "success", "message": f"已删除记录 ID={purchase_id}"})

    if user_id and sku:
        stmt = (
            select(UserPurchase)
            .where(UserPurchase.user_id == user_id.upper(), UserPurchase.sku == sku.upper())
            .order_by(UserPurchase.purchase_date.desc())
        )
        if purchase_date:
            try:
                stmt = stmt.where(UserPurchase.purchase_date == date_type.fromisoformat(purchase_date))
            except ValueError:
                pass
        result = await db.execute(stmt.limit(1))
        row = result.scalar_one_or_none()
        if not row:
            return json.dumps({"status": "error", "message": "未找到匹配的购买记录"})
        uid = row.user_id
        await db.delete(row)
        await db.commit()
        # Refresh profile (non-fatal)
        try:
            from services.user_profile import compute_profile
            await compute_profile(uid, db)
        except Exception as exc:
            logger.warning("Profile refresh failed after delete_purchase for %s: %s", uid, exc)
        return json.dumps({"status": "success", "message": f"已删除: ID={row.id} | {row.user_id} | {row.sku} | {row.product_name} | x{row.quantity} | {row.purchase_date}"}, ensure_ascii=False)

    return json.dumps({"status": "error", "message": "缺少删除参数"})


@tool
async def update_profile(user_id: str, field: str, value: str) -> str:
    """修改用户画像字段。

    Args:
        user_id: 用户ID，如 KH3734
        field: 要修改的字段，如 customer_type / value_tier
        value: 新值
    """
    from sqlalchemy import select
    from db.models import UserProfile

    db = _get_ctx()["db"]

    stmt = select(UserProfile).where(UserProfile.user_id == user_id.upper())
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if not profile:
        return json.dumps({"status": "error", "message": f"未找到用户 {user_id} 的画像"})

    profile_json = profile.profile_json or {}
    if field == "customer_type":
        profile_json.setdefault("basic_info", {})["customer_type"] = value
    elif field == "value_tier":
        profile_json["value_tier"] = value
    else:
        profile_json[field] = value
    profile.profile_json = profile_json
    await db.commit()

    return json.dumps({"status": "success", "message": f"已更新 {user_id.upper()} 的 {field} = {value}"}, ensure_ascii=False)


@tool
async def generate_user_recommendations(user_id: str) -> str:
    """为指定用户生成推荐结果。

    Args:
        user_id: 用户ID，如 KH3734
    """
    from services.llm_config_service import get_client, get_config
    from services.recommendation_graph import run_recommendation_graph

    db = _get_ctx()["db"]
    client = await get_client()
    config = await get_config()
    recs = await run_recommendation_graph(user_id.upper(), db, client, config)

    return json.dumps({"status": "success", "message": f"已为 {user_id.upper()} 生成 {len(recs)} 条推荐"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool groups
# ---------------------------------------------------------------------------

READ_TOOL_NAMES = {"query_user_profile", "query_purchases", "query_product", "query_recommendations"}
WRITE_TOOL_NAMES = {"add_purchase", "delete_purchase", "update_profile", "generate_user_recommendations"}

ALL_TOOLS = [
    query_user_profile, query_purchases, query_product, query_recommendations,
    add_purchase, delete_purchase, update_profile, generate_user_recommendations,
]

WRITE_TOOL_MAP = {
    "add_purchase": add_purchase,
    "delete_purchase": delete_purchase,
    "update_profile": update_profile,
    "generate_user_recommendations": generate_user_recommendations,
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个牙科设备推荐Agent，服务于B2B牙科设备营销场景。

你有以下工具可以使用：
- 查询工具：query_user_profile, query_purchases, query_product, query_recommendations
- 操作工具：add_purchase, delete_purchase, update_profile, generate_user_recommendations

当你需要执行操作工具时，系统会暂停并要求用户确认。请确保：
1. 参数正确无误
2. 操作前向用户说明你将做什么
3. 使用中文回答
"""


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Interrupt-aware tool node
# ---------------------------------------------------------------------------

def make_interrupt_tool_node(tools):
    """Create a tool node that interrupts before executing write tools.

    - Read tools: execute immediately (auto-approved)
    - Write tools: interrupt → wait for user confirmation → resume
    - Only the confirmed write tool executes; other write tools in the same
      message are rejected with an error ToolMessage.
    """
    tool_node = ToolNode(tools)

    async def interrupt_tool_node(state: AgentState) -> dict:
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            return {}

        write_calls = [tc for tc in last_message.tool_calls if tc["name"] in WRITE_TOOL_NAMES]

        if write_calls:
            # Interrupt: ask user to confirm the first write call
            tc = write_calls[0]
            decision = interrupt({
                "type": "confirm_tool_call",
                "tool": tc["name"],
                "args": tc["args"],
                "message": f"Agent 想要执行: {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})",
                "tool_call_id": tc["id"],
            })

            approved = decision and decision.get("approved") is True
            approved_id = tc["id"] if approved else None

            if not approved:
                # Reject ALL write tool calls
                return {"messages": [
                    ToolMessage(
                        content=json.dumps({"status": "rejected", "message": "用户取消了操作"}, ensure_ascii=False),
                        tool_call_id=tc["id"], name=tc["name"],
                    )
                    for tc in write_calls
                ]}

            # Approved: build a filtered message — keep the approved write call
            # and all read calls, replace other write calls with rejection
            approved_calls = [tc for tc in last_message.tool_calls if tc["name"] not in WRITE_TOOL_NAMES]
            approved_calls.append(tc)  # the one approved write call
            rejected_calls = [tc for tc in write_calls if tc["id"] != approved_id]

            # Execute approved calls via a modified state
            filtered_msg = last_message.model_copy(update={"tool_calls": approved_calls})
            filtered_state = {**state, "messages": state["messages"][:-1] + [filtered_msg]}
            result = await tool_node.ainvoke(filtered_state)

            # Add rejection messages for non-approved write calls
            reject_msgs = [
                ToolMessage(
                    content=json.dumps({"status": "rejected", "message": "用户取消了操作"}, ensure_ascii=False),
                    tool_call_id=tc["id"], name=tc["name"],
                )
                for tc in rejected_calls
            ]
            if reject_msgs:
                result["messages"] = list(result.get("messages", [])) + reject_msgs

            return result

        # No write tools — execute all (read tools)
        return await tool_node.ainvoke(state)

    return interrupt_tool_node


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_agent_graph(checkpointer=None):
    """Build ReAct agent with interrupt-based human-in-the-loop.

    Flow:
        agent → (has tool calls?) → interrupt_tool_node → agent → ...
                                     ↓ no tool calls
                                     END

    Write tools trigger an interrupt before execution.
    Read tools execute immediately.
    """
    from langchain_openai import ChatOpenAI
    def agent_node(state: AgentState) -> dict:
        config = _get_ctx().get("llm_config", {})

        llm = ChatOpenAI(
            model=config.get("ranking_model", "gpt-4o"),
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 4096),
            openai_api_key=config.get("api_key", ""),
            openai_api_base=config.get("base_url", "https://api.openai.com/v1"),
        )

        llm_with_tools = llm.bind_tools(ALL_TOOLS)

        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return END

    tool_node = make_interrupt_tool_node(ALL_TOOLS)

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_checkpointer = None
_agent_graph = None


def _get_checkpointer():
    """Lazily create a checkpointer for persistent memory.

    Uses InMemorySaver for simplicity. For production, consider using
    AsyncPostgresSaver or other persistent backends.
    """
    global _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        _checkpointer = MemorySaver()
    return _checkpointer


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph(checkpointer=_get_checkpointer())
    return _agent_graph


def new_thread_id() -> str:
    return str(uuid.uuid4())


async def run_chat_graph(
    thread_id: str,
    user_message: str,
    system_prompt: str,
    db: Any,
    llm_client: Any,
    llm_config: dict,
) -> dict:
    """Run the agent graph. Returns response + interrupt info if paused.

    Returns:
        {
            "response": str,
            "action": {
                "type": "confirm_tool_call",
                "tool": str,
                "args": dict,
                "message": str
            } | None
        }
    """
    set_chat_context(db=db, llm_client=llm_client, llm_config=llm_config)

    graph = get_agent_graph()
    config = {"configurable": {"thread_id": thread_id}}

    content = user_message
    if system_prompt:
        content = f"[系统上下文]\n{system_prompt}\n\n[用户消息]\n{user_message}"

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=content)]},
        config=config,
    )

    # Check for interrupt (graph paused before executing a write tool)
    state = await graph.aget_state(config)
    if state.next and state.next == ("tools",):
        pending = await _find_pending_write_call(state.values["messages"])
        if pending:
            return {
                "response": _extract_text_response(state.values["messages"]),
                "action": pending,
            }

    # No interrupt — extract final response
    response_text = ""
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = msg.content
            break

    return {"response": response_text, "action": None}


async def resume_chat_graph(
    thread_id: str,
    approved: bool,
    db: Any = None,
    llm_client: Any = None,
    llm_config: dict = None,
) -> dict:
    """Resume a paused graph after user confirmation.

    Args:
        thread_id: Conversation thread ID
        approved: True = execute the tool, False = cancel
        db: AsyncSession (injected per-request)
        llm_client: AsyncOpenAI client
        llm_config: LLM configuration dict
    """
    # Re-inject request-scoped context (the original request's session is gone)
    set_chat_context(db=db, llm_client=llm_client, llm_config=llm_config or {})

    graph = get_agent_graph()
    config = {"configurable": {"thread_id": thread_id}}

    result = await graph.ainvoke(
        Command(resume={"approved": approved}),
        config=config,
    )

    # Check if there's another interrupt (chained write calls)
    state = await graph.aget_state(config)
    if state.next and state.next == ("tools",):
        pending = await _find_pending_write_call(state.values["messages"])
        if pending:
            return {"response": _extract_text_response(state.values["messages"]), "action": pending}

    response_text = _extract_text_response(result.get("messages", []))
    return {"response": response_text, "action": None}


def _extract_text_response(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""


async def _find_pending_write_call(messages: list) -> dict | None:
    """Find the first write tool call in the messages and build a detailed preview.

    For delete_purchase, queries the DB to show which specific record will be deleted.
    """
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue

        for tc in msg.tool_calls:
            if tc["name"] not in WRITE_TOOL_NAMES:
                continue

            args = tc["args"]
            preview = f"即将执行: {tc['name']}"

            # For delete_purchase, look up the actual record that will be deleted
            if tc["name"] == "delete_purchase":
                preview = await _build_delete_preview(args)

            return {
                "type": "confirm_tool_call",
                "tool": tc["name"],
                "args": args,
                "message": preview,
                "tool_call_id": tc["id"],
            }

    return None


async def _build_delete_preview(args: dict) -> str:
    """Query the DB to show exactly which record will be deleted."""
    from sqlalchemy import select
    from db.models import UserPurchase

    db = _get_ctx().get("db")
    if not db:
        return "即将执行: delete_purchase"

    purchase_id = args.get("purchase_id")
    user_id = args.get("user_id", "")
    sku = args.get("sku", "")
    purchase_date = args.get("purchase_date", "")

    if purchase_id:
        stmt = select(UserPurchase).where(UserPurchase.id == int(purchase_id))
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            return f"将删除记录: ID={row.id} | {row.user_id} | {row.sku} | {row.product_name} | x{row.quantity} | {row.purchase_date}"
        return f"将删除记录: ID={purchase_id}"

    if user_id and sku:
        stmt = (
            select(UserPurchase)
            .where(UserPurchase.user_id == user_id.upper(), UserPurchase.sku == sku.upper())
            .order_by(UserPurchase.purchase_date.desc())
        )
        if purchase_date:
            try:
                stmt = stmt.where(UserPurchase.purchase_date == date_type.fromisoformat(purchase_date))
            except ValueError:
                pass
        result = await db.execute(stmt.limit(1))
        row = result.scalar_one_or_none()
        if row:
            return f"将删除记录: ID={row.id} | {row.user_id} | {row.sku} | {row.product_name} | x{row.quantity} | {row.purchase_date}"
        return f"未找到匹配记录: {user_id} | {sku}"

    return "即将执行: delete_purchase"


# ---------------------------------------------------------------------------
# Streaming APIs
# ---------------------------------------------------------------------------

def _format_tool_input(tool_name: str, tool_input: dict) -> str:
    """Format tool input for display."""
    if not tool_input:
        return ""
    try:
        return json.dumps(tool_input, ensure_ascii=False, indent=2)
    except:
        return str(tool_input)


def _format_tool_output(output: Any) -> str:
    """Format tool output for display."""
    if output is None:
        return "无结果"
    if isinstance(output, str):
        return output[:500] + "..." if len(output) > 500 else output
    if isinstance(output, dict):
        try:
            return json.dumps(output, ensure_ascii=False, indent=2)[:500]
        except:
            return str(output)[:500]
    return str(output)[:500]


async def run_chat_graph_stream(
    thread_id: str,
    user_message: str,
    system_prompt: str,
    db: Any,
    llm_client: Any,
    llm_config: dict,
):
    """Run the agent graph with streaming response.

    Yields dicts with different component types:
        {"type": "thread_id", "thread_id": "..."}
        {"type": "thinking", "content": "..."}           # 思考过程
        {"type": "tool_call", "tool": "...", "input": "..."}  # 工具调用
        {"type": "tool_result", "tool": "...", "output": "..."}  # 工具结果
        {"type": "token", "content": "..."}              # 最终回复token
        {"type": "action", "action": {...}}              # 需要确认的操作
        {"type": "error", "message": "..."}              # 错误
        {"type": "done", "response": "..."}              # 完成
    """
    set_chat_context(db=db, llm_client=llm_client, llm_config=llm_config)

    graph = get_agent_graph()
    config = {"configurable": {"thread_id": thread_id}}

    # Send thread_id first
    yield {"type": "thread_id", "thread_id": thread_id}

    content = user_message
    if system_prompt:
        content = f"[系统上下文]\n{system_prompt}\n\n[用户消息]\n{user_message}"

    # Track tool calls and responses
    full_response = ""
    current_tool_calls = {}

    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=content)]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        name = event.get("name", "")

        # LLM 思考过程（包括工具调用决策）
        if kind == "on_chat_model_start":
            yield {"type": "thinking", "content": "正在思考...", "component": "thinking"}

        elif kind == "on_chat_model_end":
            output = event.get("data", {}).get("output")
            if output and hasattr(output, "tool_calls") and output.tool_calls:
                # 有工具调用
                for tc in output.tool_calls:
                    tool_name = tc.get("name", "unknown")
                    tool_args = tc.get("args", {})
                    current_tool_calls[tc.get("id", "")] = {
                        "name": tool_name,
                        "args": tool_args,
                    }
                    yield {
                        "type": "tool_call",
                        "component": "tool_call",
                        "tool": tool_name,
                        "input": _format_tool_input(tool_name, tool_args),
                        "content": f"调用工具: {tool_name}",
                    }
            elif output and hasattr(output, "content") and output.content:
                # LLM 的思考文本（不是最终回复）
                if not full_response:  # 只在还没有最终回复时显示
                    thinking_text = output.content
                    if thinking_text and len(thinking_text) > 10:
                        yield {
                            "type": "thinking",
                            "component": "thinking",
                            "content": thinking_text[:300] + ("..." if len(thinking_text) > 300 else ""),
                        }

        # 工具执行开始
        elif kind == "on_tool_start":
            tool_name = name or event.get("metadata", {}).get("langgraph_node", "")
            yield {
                "type": "thinking",
                "component": "tool_execution",
                "content": f"正在执行: {tool_name}...",
            }

        # 工具执行结束
        elif kind == "on_tool_end":
            tool_name = name or "unknown"
            output = event.get("data", {}).get("output")
            formatted_output = _format_tool_output(output)

            # 检查是否是错误
            if isinstance(output, str) and ("error" in output.lower() or "失败" in output):
                yield {
                    "type": "tool_result",
                    "component": "tool_error",
                    "tool": tool_name,
                    "output": formatted_output,
                    "content": f"工具执行失败: {tool_name}",
                }
            else:
                yield {
                    "type": "tool_result",
                    "component": "tool_result",
                    "tool": tool_name,
                    "output": formatted_output,
                    "content": f"工具结果: {tool_name}",
                }

        # 最终回复 token
        elif kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                token = chunk.content
                full_response += token
                yield {"type": "token", "component": "response", "content": token}

        # 链执行（可以用来显示流程）
        elif kind == "on_chain_start":
            node_name = event.get("name", "")
            if node_name in ["agent", "tools"]:
                yield {
                    "type": "thinking",
                    "component": "flow",
                    "content": f"{'🤖 Agent' if node_name == 'agent' else '🔧 工具'}节点执行中...",
                }

    # Check for interrupt (graph paused before executing a write tool)
    state = await graph.aget_state(config)
    if state.next and state.next == ("tools",):
        pending = await _find_pending_write_call(state.values["messages"])
        if pending:
            yield {"type": "action", "component": "action", "action": pending}
            yield {"type": "done", "component": "done", "response": _extract_text_response(state.values["messages"])}
            return

    yield {"type": "done", "component": "done", "response": full_response}


async def resume_chat_graph_stream(
    thread_id: str,
    approved: bool,
    db: Any = None,
    llm_client: Any = None,
    llm_config: dict = None,
):
    """Resume a paused graph with streaming response.

    Yields dicts with different component types.
    """
    set_chat_context(db=db, llm_client=llm_client, llm_config=llm_config or {})

    graph = get_agent_graph()
    config = {"configurable": {"thread_id": thread_id}}

    if approved:
        yield {"type": "thinking", "component": "thinking", "content": "用户已确认，正在执行操作..."}
    else:
        yield {"type": "thinking", "component": "thinking", "content": "用户已取消操作"}

    # Use astream_events for streaming
    full_response = ""
    async for event in graph.astream_events(
        Command(resume={"approved": approved}),
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        name = event.get("name", "")

        # LLM 思考过程
        if kind == "on_chat_model_start":
            yield {"type": "thinking", "component": "thinking", "content": "正在思考..."}

        elif kind == "on_chat_model_end":
            output = event.get("data", {}).get("output")
            if output and hasattr(output, "tool_calls") and output.tool_calls:
                for tc in output.tool_calls:
                    tool_name = tc.get("name", "unknown")
                    tool_args = tc.get("args", {})
                    yield {
                        "type": "tool_call",
                        "component": "tool_call",
                        "tool": tool_name,
                        "input": _format_tool_input(tool_name, tool_args),
                        "content": f"调用工具: {tool_name}",
                    }

        # 工具执行
        elif kind == "on_tool_start":
            tool_name = name or event.get("metadata", {}).get("langgraph_node", "")
            yield {
                "type": "thinking",
                "component": "tool_execution",
                "content": f"正在执行: {tool_name}...",
            }

        elif kind == "on_tool_end":
            tool_name = name or "unknown"
            output = event.get("data", {}).get("output")
            formatted_output = _format_tool_output(output)
            yield {
                "type": "tool_result",
                "component": "tool_result",
                "tool": tool_name,
                "output": formatted_output,
                "content": f"工具结果: {tool_name}",
            }

        # 最终回复 token
        elif kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                token = chunk.content
                full_response += token
                yield {"type": "token", "component": "response", "content": token}

        # 链执行
        elif kind == "on_chain_start":
            node_name = event.get("name", "")
            if node_name in ["agent", "tools"]:
                yield {
                    "type": "thinking",
                    "component": "flow",
                    "content": f"{'🤖 Agent' if node_name == 'agent' else '🔧 工具'}节点执行中...",
                }

    # Check if there's another interrupt (chained write calls)
    state = await graph.aget_state(config)
    if state.next and state.next == ("tools",):
        pending = await _find_pending_write_call(state.values["messages"])
        if pending:
            yield {"type": "action", "component": "action", "action": pending}

    yield {"type": "done", "component": "done", "response": full_response or _extract_text_response(state.values.get("messages", []))}
