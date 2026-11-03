"""GitHub 载荷适配：comment / issue / pull_request 归一化。"""

from __future__ import annotations

from typing import Any

from agentsentinel.api.adapters import parse_github_event
from agentsentinel.api.ingress import IngressEvent

SOURCE = "github"


def test_comment_body_becomes_text_with_issue_labels() -> None:
    payload: dict[str, Any] = {
        "comment": {"body": "@AgentSentinel 修复登录"},
        "issue": {"labels": [{"name": "bug"}, {"name": "backend"}]},
    }

    event = parse_github_event(payload, SOURCE)

    assert event == IngressEvent(
        source=SOURCE,
        text="@AgentSentinel 修复登录",
        labels=("bug", "backend"),
    )


def test_comment_without_issue_has_no_labels() -> None:
    payload: dict[str, Any] = {"comment": {"body": "/run-test smoke"}}

    event = parse_github_event(payload, SOURCE)

    assert event == IngressEvent(source=SOURCE, text="/run-test smoke")


def test_comment_precedes_issue_when_both_present() -> None:
    payload: dict[str, Any] = {
        "comment": {"body": "评论内容"},
        "issue": {"title": "标题", "body": "正文"},
        "pull_request": {"title": "PR 标题"},
    }

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.text == "评论内容"


def test_non_string_comment_body_falls_through_to_issue() -> None:
    payload: dict[str, Any] = {"comment": {"body": 123}, "issue": {"title": "标题"}}

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.text == "标题"


def test_issue_title_and_body_are_joined_by_newline() -> None:
    payload: dict[str, Any] = {
        "issue": {
            "title": "登录失败",
            "body": "复现步骤",
            "labels": [{"name": "Bug"}],
        }
    }

    event = parse_github_event(payload, SOURCE)

    assert event == IngressEvent(
        source=SOURCE,
        text="登录失败\n复现步骤",
        labels=("Bug",),
    )


def test_issue_without_title_returns_body_only() -> None:
    payload: dict[str, Any] = {"issue": {"body": "只有正文"}}

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.text == "只有正文"


def test_issue_without_body_returns_title_only() -> None:
    payload: dict[str, Any] = {"issue": {"title": "只有标题", "body": None}}

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.text == "只有标题"


def test_issue_whitespace_only_text_returns_none() -> None:
    payload: dict[str, Any] = {"issue": {"title": "   ", "body": "\n"}}

    assert parse_github_event(payload, SOURCE) is None


def test_issue_without_text_fields_returns_none() -> None:
    payload: dict[str, Any] = {"issue": {"title": 123, "body": None}}

    assert parse_github_event(payload, SOURCE) is None


def test_pull_request_uses_issue_rule() -> None:
    payload: dict[str, Any] = {
        "pull_request": {
            "title": "修复登录",
            "body": "变更说明",
            "labels": [{"name": "bug"}],
        }
    }

    event = parse_github_event(payload, SOURCE)

    assert event == IngressEvent(
        source=SOURCE,
        text="修复登录\n变更说明",
        labels=("bug",),
    )


def test_issue_precedes_pull_request() -> None:
    payload: dict[str, Any] = {
        "issue": {"title": "Issue 标题"},
        "pull_request": {"title": "PR 标题"},
    }

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.text == "Issue 标题"


def test_labels_dedupe_preserve_order_and_skip_invalid_types() -> None:
    payload: dict[str, Any] = {
        "issue": {
            "title": "标题",
            "labels": [
                {"name": "bug"},
                {"name": "bug"},
                {"name": 123},
                "backend",
                {"other": "x"},
                {"name": "feature"},
            ],
        }
    }

    event = parse_github_event(payload, SOURCE)

    assert event is not None
    assert event.labels == ("bug", "feature")


def test_unrecognized_payload_returns_none() -> None:
    payload: dict[str, Any] = {"zen": "Keep it logically awesome."}

    assert parse_github_event(payload, SOURCE) is None


def test_non_mapping_sections_are_ignored() -> None:
    payload: dict[str, Any] = {"comment": "text", "issue": ["title"], "pull_request": 1}

    assert parse_github_event(payload, SOURCE) is None
