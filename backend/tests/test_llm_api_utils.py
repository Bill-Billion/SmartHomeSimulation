from __future__ import annotations

import pytest

from backend.app.llm_api_utils import extract_json_object, extract_message_content, normalize_chat_endpoint


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/chat/completions"),
        ("https://example.com/proxy/v1", "https://example.com/proxy/v1/chat/completions"),
        ("https://example.com/proxy/v1/chat/completions", "https://example.com/proxy/v1/chat/completions"),
        ("https://example.com/proxy", "https://example.com/proxy/v1/chat/completions"),
    ],
)
def test_normalize_chat_endpoint(base_url: str, expected: str) -> None:
    assert normalize_chat_endpoint(base_url) == expected


def test_extract_message_content_from_string() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "  {\"ok\": true}  ",
                }
            }
        ]
    }
    assert extract_message_content(payload) == "{\"ok\": true}"


def test_extract_message_content_from_list() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "line 1"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                        {"type": "text", "text": "line 2"},
                    ],
                }
            }
        ]
    }
    assert extract_message_content(payload) == "line 1\nline 2"


def test_extract_message_content_raises_when_missing() -> None:
    with pytest.raises(ValueError):
        extract_message_content({"choices": []})


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"room_overrides":[]}', {"room_overrides": []}),
        ("```json\n{\"a\":1}\n```", {"a": 1}),
        ("result:\n{\"foo\":\"bar\"}\nthanks", {"foo": "bar"}),
    ],
)
def test_extract_json_object(content: str, expected: dict[str, object]) -> None:
    assert extract_json_object(content) == expected


def test_extract_json_object_raises_for_invalid_content() -> None:
    with pytest.raises(ValueError):
        extract_json_object("no json here")
