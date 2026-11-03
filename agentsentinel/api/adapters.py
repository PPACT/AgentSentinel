"""GitHub Webhook 载荷适配：comment / issue / pull_request 归一为 ``IngressEvent``。

只读取本系统需要的字段，未知结构返回 ``None``，由路由层统一回 400。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentsentinel.api.ingress import IngressEvent


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _labels(container: Mapping[str, Any] | None) -> tuple[str, ...]:
    """取 ``labels[*].name`` 中的字符串值，去重并保留首次出现顺序。"""
    if container is None:
        return ()
    raw = container.get("labels")
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = _as_mapping(item)
        if label is None:
            continue
        name = label.get("name")
        if isinstance(name, str) and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def _title_body_text(container: Mapping[str, Any]) -> str | None:
    """把 title 与 body 以换行连接；缺失侧按空串，strip 后为空返回 None。"""
    title = container.get("title")
    body = container.get("body")
    if not isinstance(title, str) and not isinstance(body, str):
        return None
    title_text = title if isinstance(title, str) else ""
    body_text = body if isinstance(body, str) else ""
    stripped = f"{title_text}\n{body_text}".strip()
    return stripped or None


def parse_github_event(payload: Mapping[str, Any], source: str) -> IngressEvent | None:
    """把 GitHub 事件载荷归一为入口事件；无法识别时返回 ``None``。

    优先取评论正文；其次取 issue 的标题/正文；最后取 pull_request 的同名字段。
    评论事件的标签仍取自所属 issue。
    """
    comment = _as_mapping(payload.get("comment"))
    if comment is not None:
        body = comment.get("body")
        if isinstance(body, str):
            return IngressEvent(
                source=source,
                text=body,
                labels=_labels(_as_mapping(payload.get("issue"))),
            )

    for key in ("issue", "pull_request"):
        container = _as_mapping(payload.get(key))
        if container is None:
            continue
        text = _title_body_text(container)
        if text is not None:
            return IngressEvent(source=source, text=text, labels=_labels(container))

    return None
