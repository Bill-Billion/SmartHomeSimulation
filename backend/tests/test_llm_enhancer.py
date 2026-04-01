from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import backend.app.llm_enhancer as llm_enhancer
from backend.app.models import FloorplanSpec, Point2D, RoomSpec, RoomType, SourceType
from backend.app.scene_builder import build_scene_spec


def _ready_config() -> llm_enhancer.LlmRequestConfig:
    return llm_enhancer.LlmRequestConfig(
        enabled=True,
        base_url="https://example.com/v1",
        model="demo-model",
        api_key="secret-key",
    )


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, post_impl) -> None:
    class _FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return post_impl(*args, **kwargs)

    monkeypatch.setattr(llm_enhancer.httpx, "Client", _FakeClient)


def _request_failure(monkeypatch: pytest.MonkeyPatch, post_impl, *, with_thumbnail: bool = False):
    _install_fake_client(monkeypatch, post_impl)
    evidence = {"rooms": [], "warnings": []}
    if with_thumbnail:
        evidence["thumbnail_data_url"] = "data:image/png;base64,AAA"
    with pytest.raises(llm_enhancer.LlmEnhancementFailure) as exc_info:
        llm_enhancer._request_llm_enhancement(_ready_config(), evidence)
    return exc_info.value


def _json_response(url: str, payload: dict, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=payload,
        request=httpx.Request("POST", url),
    )


def _make_floorplan() -> FloorplanSpec:
    room_polygon = [
        Point2D(x=-2.0, z=-2.0),
        Point2D(x=2.0, z=-2.0),
        Point2D(x=2.0, z=2.0),
        Point2D(x=-2.0, z=2.0),
    ]
    room = RoomSpec(
        room_id="room_1",
        name="通用房间 1",
        room_type=RoomType.GENERIC,
        polygon=room_polygon,
        area_sqm=16.0,
        confidence=0.66,
    )
    return FloorplanSpec(
        source_type=SourceType.PNG,
        bounds_width_m=4.0,
        bounds_depth_m=4.0,
        scale_m_per_unit=1.0,
        outer_walls=[],
        inner_walls=[],
        openings=[],
        rooms=[room],
        confidence=0.66,
        warnings=[],
    )


def test_request_timeout_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=httpx.Request("POST", url))),
    )

    assert failure.category == "timeout"
    assert failure.user_warning == "AI 服务请求超时，已按规则结果继续生成。"


def test_request_connect_error_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("connect failed", request=httpx.Request("POST", url))),
    )

    assert failure.category == "connect_error"
    assert failure.user_warning == "AI 服务连接失败，已按规则结果继续生成。"


def test_request_auth_failure_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: _json_response(url, {"error": {"message": "invalid api key"}}, status_code=401),
    )

    assert failure.category == "auth_failed"
    assert failure.user_warning == "AI 服务鉴权失败，已按规则结果继续生成。"


def test_request_not_found_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: _json_response(url, {"error": {"message": "not found"}}, status_code=404),
    )

    assert failure.category == "endpoint_not_found"
    assert failure.user_warning == "AI 服务地址、接口路径或模型名称不兼容，已按规则结果继续生成。"


def test_request_image_not_supported_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: _json_response(
            url,
            {"error": {"message": "This model does not support image_url content."}},
            status_code=422,
        ),
        with_thumbnail=True,
    )

    assert failure.category == "image_not_supported"
    assert failure.user_warning == "当前 AI 服务不支持图片输入，已按规则结果继续生成。"


def test_request_non_json_content_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: _json_response(
            url,
            {"choices": [{"message": {"content": "not a json payload"}}]},
        ),
    )

    assert failure.category == "content_not_json"
    assert failure.user_warning == "AI 返回的内容不是 JSON 格式，已按规则结果继续生成。"


def test_request_non_json_response_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def _response(url, **_kwargs):
        return httpx.Response(
            200,
            text="<!doctype html><html>bad gateway</html>",
            request=httpx.Request("POST", url),
        )

    failure = _request_failure(monkeypatch, _response)

    assert failure.category == "response_not_json"
    assert failure.user_warning == "AI 服务返回的响应不是合法 JSON，已按规则结果继续生成。"


def test_request_schema_invalid_maps_to_specific_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = _request_failure(
        monkeypatch,
        lambda url, **_kwargs: _json_response(
            url,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"room_overrides":[{"room_id":"room_1","room_type":"kitchen",'
                                '"wall_preference":"east"}]}'
                            )
                        }
                    }
                ]
            },
        ),
    )

    assert failure.category == "schema_validation"
    assert failure.user_warning == "AI 返回结构不符合约束，已按规则结果继续生成。"


def test_apply_scene_llm_enhancements_surfaces_specific_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "llm_warning.png"
    image_path.write_bytes(b"fake")
    floorplan = _make_floorplan()
    scene = build_scene_spec("scene_llm_warning", floorplan)

    def _raise_failure(_config, _evidence):
        raise llm_enhancer.LlmEnhancementFailure(
            category="auth_failed",
            user_warning="AI 服务鉴权失败，已按规则结果继续生成。",
            log_message="endpoint=https://example.com/v1/chat/completions status=401",
        )

    monkeypatch.setattr(llm_enhancer, "_request_llm_enhancement", _raise_failure)

    updated_floorplan, furniture_hints = llm_enhancer.apply_scene_llm_enhancements(
        image_path,
        floorplan,
        scene,
        _ready_config(),
    )

    assert furniture_hints == {}
    assert any("AI 服务鉴权失败" in warning for warning in updated_floorplan.warnings)
