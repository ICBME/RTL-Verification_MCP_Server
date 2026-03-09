from __future__ import annotations

import uuid
import weakref
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

logger = logging.getLogger(__name__)

# WeakKeyDictionary: session 对象 → 工作区 key 字符串
# session 对象 GC 时条目自动删除，无内存泄漏
_weak_registry: weakref.WeakKeyDictionary[object, str] = weakref.WeakKeyDictionary()

# 备用：id(session) → key，同时持有强引用防止 id 复用
_id_registry: dict[int, str]    = {}
_id_strong_refs: dict[int, object] = {}


def workspace_key(ctx: "Context") -> str:
    """
    返回当前 session 的唯一工作区 key（16 位十六进制字符串）。

    同一 session 的每次调用返回相同值；
    不同 session 的调用返回不同值；
    session 结束后对应条目自动释放。
    """
    try:
        session = ctx.request_context.session
    except Exception as exc:
        raise RuntimeError(
            "无法访问 ctx.request_context.session。"
            "请确认使用 streamable-http 传输，且 FastMCP 版本 >= 1.0。"
        ) from exc

    # 主路径：WeakKeyDictionary
    try:
        if session not in _weak_registry:
            _weak_registry[session] = _new_key()
            logger.debug("new workspace key for session %s", type(session).__name__)
        return _weak_registry[session]

    except TypeError:
        # 备用路径：session 对象不可弱引用（定义了 __slots__ 无 __weakref__）
        obj_id = id(session)
        if obj_id not in _id_registry:
            key = _new_key()
            _id_registry[obj_id]    = key
            _id_strong_refs[obj_id] = session   # 防止 GC 导致 id 复用
            logger.warning(
                "session 对象 %s 不可弱引用，使用 id() 备用路径。"
                "session 结束时请调用 release_by_id(%d) 清理。",
                type(session).__name__, obj_id,
            )
        return _id_registry[obj_id]


def release_by_id(obj_id: int) -> None:
    """释放备用路径里的强引用（在 session 结束时调用）。"""
    _id_strong_refs.pop(obj_id, None)
    _id_registry.pop(obj_id, None)


def _new_key() -> str:
    return uuid.uuid4().hex[:16]
