"""Prompt cache 实测工具（零依赖，只用标准库）。

用法（在控制台 PTY / 对话里执行）：

    uv run python scripts/cache_probe.py sleep --seconds 360
    uv run python scripts/cache_probe.py probe
    uv run python scripts/cache_probe.py probe --settle 3 --repeat 3

``probe`` 向 ``ANTHROPIC_BASE_URL``（未设置则官方端点）连续发送工具/系统前缀完全
相同的请求，打印每次响应的 ``usage.cache_read_input_tokens``。第二次起大于 0 说明
缓存链路（CC → 代理 → 上游）可用；始终为 0 则先怀疑代理/端点没有回传缓存字段，
而不是会话本身。脚本不会打印任何密钥。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

SYSTEM_FILLER = ("[cache-probe] stable prefix block. " * 120).strip()
USER_TEXT = "Reply with the single word: ok"
REQUEST_TIMEOUT_SECONDS = 120.0


def auth_headers() -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    elif api_key:
        headers["x-api-key"] = api_key
    return headers


def default_model() -> str:
    for name in (
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "ANTHROPIC_MODEL",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return "claude-haiku-4-5"


def messages_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        base = "https://api.anthropic.com"
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def request_body(model: str, messages: list[dict[str, Any]], *, cache_control: bool) -> bytes:
    system: Any = SYSTEM_FILLER
    if cache_control:
        system = [
            {
                "type": "text",
                "text": SYSTEM_FILLER,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    body = {
        "model": model,
        "max_tokens": 1,
        "system": system,
        "messages": messages,
    }
    # 规范化序列化：同一进程内每次发送的字节完全一致，排除序列化漂移。
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def post(endpoint: str, body: bytes) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(endpoint, data=body, headers=auth_headers(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"error": str(exc)}
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = {"error": raw[:200].decode("utf-8", "replace")}
    return status, data if isinstance(data, dict) else {"value": data}


def usage_line(attempt: int, status: int, data: dict[str, Any]) -> str:
    usage = data.get("usage")
    if status != 200 or not isinstance(usage, dict):
        error = str(data.get("error") or data)[:160]
        return f"attempt={attempt} status={status} error={error}"
    return (
        f"attempt={attempt} status={status} "
        f"input={int(usage.get('input_tokens') or 0)} "
        f"cache_read={int(usage.get('cache_read_input_tokens') or 0)} "
        f"cache_write={int(usage.get('cache_creation_input_tokens') or 0)} "
        f"output={int(usage.get('output_tokens') or 0)}"
    )


def response_text(data: dict[str, Any]) -> str:
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()


def cmd_probe(args: argparse.Namespace) -> int:
    base_url = (args.base_url or os.environ.get("ANTHROPIC_BASE_URL", "")).strip()
    endpoint = messages_url(base_url)
    model = (args.model or default_model()).strip()
    cache_control = not args.no_cache_control
    messages: list[dict[str, Any]] = [{"role": "user", "content": USER_TEXT}]
    body = request_body(model, messages, cache_control=cache_control)
    print(f"endpoint={endpoint} model={model} bytes={len(body)} grow={args.grow}", flush=True)

    cache_reads: list[int] = []
    for attempt in range(1, max(1, args.repeat) + 1):
        if attempt > 1 and args.settle > 0:
            time.sleep(args.settle)
        status, data = post(endpoint, body)
        print(usage_line(attempt, status, data), flush=True)
        usage = data.get("usage")
        if status == 200 and isinstance(usage, dict):
            cache_reads.append(int(usage.get("cache_read_input_tokens") or 0))
        if args.grow and status == 200:
            # 只追加尾部消息：tools + system + 旧 messages 前缀保持不变。
            messages = [
                *messages,
                {"role": "assistant", "content": response_text(data) or "ok"},
                {"role": "user", "content": f"Turn {attempt + 1}: reply ok again."},
            ]
            body = request_body(model, messages, cache_control=cache_control)

    if not cache_reads:
        print("verdict=ERROR 请求失败，无法判断缓存")
        return 1
    if len(cache_reads) == 1:
        print("verdict=UNKNOWN 只有一次成功请求，至少跑 --repeat 2")
        return 0
    hit = cache_reads[-1] > 0
    print(f"verdict={'HIT' if hit else 'MISS'} cache_read={cache_reads}")
    if not hit and args.grow:
        print(
            "提示：前缀不变、只在尾部追加消息仍为 0，说明代理在 system/前缀里写入了"
            "随请求变化的动态内容（如 token 计数）。",
            flush=True,
        )
    elif not hit:
        print(
            "提示：相同请求重复仍为 0，说明代理/端点未回传缓存字段或前缀被改写；"
            "与 CC 会话本身无关。",
            flush=True,
        )
    return 0


def cmd_sleep(args: argparse.Namespace) -> int:
    seconds = max(0.0, args.seconds)
    print(
        f"sleep start {time.strftime('%Y-%m-%d %H:%M:%S')} ({seconds:.0f}s)",
        flush=True,
    )
    time.sleep(seconds)
    print(
        f"sleep end {time.strftime('%Y-%m-%d %H:%M:%S')} —— 现在发一句话，"
        "观察 Token 看板的 cache_read",
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prompt cache 实测工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sleep = sub.add_parser("sleep", help="空等指定秒数，用于越过 5 分钟 TTL")
    sleep.add_argument("--seconds", type=float, default=360.0)
    sleep.set_defaults(func=cmd_sleep)

    probe = sub.add_parser("probe", help="重复发送相同请求并打印 cache 命中字段")
    probe.add_argument("--base-url", default="")
    probe.add_argument("--model", default="")
    probe.add_argument("--repeat", type=int, default=2)
    probe.add_argument("--settle", type=float, default=3.0)
    probe.add_argument(
        "--grow",
        action="store_true",
        help="每次只在尾部追加一轮对话，验证正常增长时前缀仍命中",
    )
    probe.add_argument(
        "--no-cache-control",
        action="store_true",
        help="不发送 cache_control 标记（DeepSeek 自动缓存可忽略该标记）",
    )
    probe.set_defaults(func=cmd_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
