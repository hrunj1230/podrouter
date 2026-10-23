"""Unit tests for AI YAML generation contracts in podrouter.planner and podrouter.web."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

from podrouter.config import load_config
from podrouter.errors import ConfigError
from podrouter.planner import build_plan, plan_pod, read_plan_fields
from podrouter.web import plan_from_body

CATALOG = [
    {
        "id": "NVIDIA A40",
        "name": "NVIDIA A40",
        "memory_gb": 48,
        "secure": {
            "offered": True,
            "price": 0.59,
            "stock": "High",
            "offer": 0.59,
        },
        "community": {
            "offered": True,
            "price": 0.4,
            "stock": None,
            "offer": None,
        },
    },
    {
        "id": "NVIDIA GeForce RTX 4090",
        "name": "NVIDIA GeForce RTX 4090",
        "memory_gb": 24,
        "secure": {
            "offered": True,
            "price": 0.89,
            "stock": "Low",
            "offer": 0.89,
        },
        "community": {
            "offered": False,
            "price": None,
            "stock": None,
            "offer": None,
        },
    },
]

VALID_MODEL_JSON = {
    "gpu_id": "NVIDIA A40",
    "cloud": "SECURE",
    "model": "bartowski/Example-7B-GGUF:Q4_K_M",
    "context_tokens": 8192,
    "reason": "7B 공개 가중치라 A40이면 충분합니다",
}

TEST_API_KEY = "test-xai-key"
EXPECTED_ARGS = (
    "-hf bartowski/Example-7B-GGUF:Q4_K_M --host 0.0.0.0 --port 8080 -c 8192 -ngl 99 --jinja"
)
EXPECTED_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda13"


def make_completions_body(content: Any) -> bytes:
    if isinstance(content, (dict, list)):
        content_str = json.dumps(content)
    elif content is None:
        content_str = ""
    else:
        content_str = str(content)
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "grok-4.7",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content_str,
                },
                "finish_reason": "stop",
            }
        ],
    }
    return json.dumps(body).encode("utf-8")


@pytest.fixture(autouse=True)
def _guard_default_post(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_default_post(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("podrouter.planner.default_post should not be called")

    monkeypatch.setattr("podrouter.planner.default_post", _fail_default_post)


class PostRecorder:
    def __init__(self, response: tuple[int, bytes] | None = None):
        self.calls: list[dict[str, Any]] = []
        self._response = response or (200, make_completions_body(VALID_MODEL_JSON))

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> tuple[int, bytes]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return self._response


def test_plan_pod_success(tmp_path: Path) -> None:
    post = PostRecorder()
    result = plan_pod(
        "qwen38-27b",
        "7B 공개 모델을 올려줘",
        CATALOG,
        api_key=TEST_API_KEY,
        post=post,
    )

    # 1. 반환 키 검증
    expected_keys = {
        "yaml",
        "document",
        "gpu_id",
        "cloud",
        "model",
        "context_tokens",
        "reason",
        "hourly_price",
    }
    assert expected_keys.issubset(result.keys())

    # 2. post 호출 계약 검증
    assert len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == "https://api.x.ai/v1/chat/completions"
    assert call["payload"]["model"] == "grok-4.7"

    response_format = call["payload"].get("response_format", {})
    assert response_format.get("type") == "json_schema"
    json_schema = response_format.get("json_schema", {})
    schema = json_schema.get("schema", {})
    assert schema.get("additionalProperties") is False

    auth_header = call["headers"].get("Authorization") or call["headers"].get("authorization")
    assert auth_header == f"Bearer {TEST_API_KEY}"

    # 3. 비밀 값 노출 방지
    assert TEST_API_KEY not in result["yaml"]
    assert TEST_API_KEY not in result["reason"]

    # 4. 반환 데이터 값 검증
    assert result["gpu_id"] == "NVIDIA A40"
    assert result["cloud"] == "SECURE"
    assert result["model"] == "bartowski/Example-7B-GGUF:Q4_K_M"
    assert result["context_tokens"] == 8192
    assert result["reason"] == "7B 공개 가중치라 A40이면 충분합니다"
    assert result["hourly_price"] == 0.59

    # 5. document 구조 검증
    doc = result["document"]
    assert doc["name"] == "qwen38-27b"
    assert doc["args"] == EXPECTED_ARGS
    assert doc["image"] == EXPECTED_IMAGE
    assert doc["gpu"]["candidates"] == ["NVIDIA A40"]
    assert doc["gpu"]["count"] == 1
    assert doc["gpu"]["cloud"] == "SECURE"
    assert "max_price_per_hour" not in doc.get("gpu", {})
    assert "env" not in doc
    assert doc["port"] == 8080
    assert doc["storage"]["container_disk_gb"] == 60
    assert doc["storage"]["network_volume_id"] is None
    assert doc["lifecycle"]["ttl"] == "3h"
    assert "idle_timeout" not in doc["lifecycle"]
    assert doc["readiness"]["timeout_sec"] == 900
    assert doc["timeouts"]["boot_sec"] == 600

    # 6. yaml 텍스트 검증
    yaml_text = result["yaml"]
    assert "HF_TOKEN" not in yaml_text
    assert "max_price_per_hour" not in yaml_text
    parsed_yaml = yaml.safe_load(yaml_text)
    assert parsed_yaml["args"] == EXPECTED_ARGS
    assert parsed_yaml["image"] == EXPECTED_IMAGE
    assert "env" not in parsed_yaml
    assert "max_price_per_hour" not in parsed_yaml.get("gpu", {})

    # 7. load_config(path) 로 예외 없이 읽히는지 확인
    yaml_file = tmp_path / "server.yaml"
    yaml_file.write_text(yaml_text, encoding="utf-8")
    cfg = load_config(yaml_file)
    assert cfg.name == "qwen38-27b"
    assert cfg.args == EXPECTED_ARGS
    assert cfg.image == EXPECTED_IMAGE
    assert cfg.candidates == ("NVIDIA A40",)
    assert cfg.gpu_count == 1
    assert cfg.cloud == "SECURE"
    assert cfg.container_disk_gb == 60
    assert cfg.network_volume_id is None
    assert cfg.port == 8080
    assert cfg.ttl_text == "3h"
    assert cfg.max_price_per_hour is None


def test_plan_pod_rejects_unknown_gpu_id() -> None:
    bad_model = dict(VALID_MODEL_JSON, gpu_id="NVIDIA H100")
    post = PostRecorder((200, make_completions_body(bad_model)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_pod_rejects_unoffered_cloud() -> None:
    # 4090은 community offered가 False
    bad_model = dict(VALID_MODEL_JSON, gpu_id="NVIDIA GeForce RTX 4090", cloud="COMMUNITY")
    post = PostRecorder((200, make_completions_body(bad_model)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_pod_rejects_secure_tier_without_price() -> None:
    catalog_with_none_price = [
        {
            "id": "NVIDIA A40",
            "name": "NVIDIA A40",
            "memory_gb": 48,
            "secure": {"offered": True, "price": None, "stock": "High", "offer": None},
            "community": {"offered": True, "price": 0.4, "stock": None, "offer": None},
        }
    ]
    post = PostRecorder((200, make_completions_body(VALID_MODEL_JSON)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", catalog_with_none_price, api_key=TEST_API_KEY, post=post)


@pytest.mark.parametrize(
    "bad_model_name",
    [
        "bartowski/'Example'",
        'bartowski/"Example"',
        "bartowski/Example 7B",
        "bartowski/Example;rm",
        "bartowski/Example$FOO",
        "bartowski/Example`bad`",
        "bartowski/Example\n7B",
    ],
)
def test_plan_pod_rejects_forbidden_chars_in_model(bad_model_name: str) -> None:
    bad_model = dict(VALID_MODEL_JSON, model=bad_model_name)
    post = PostRecorder((200, make_completions_body(bad_model)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


@pytest.mark.parametrize(
    "bad_tokens",
    [
        1024,
        262145,
        True,
    ],
)
def test_plan_pod_rejects_invalid_context_tokens(bad_tokens: Any) -> None:
    bad_model = dict(VALID_MODEL_JSON, context_tokens=bad_tokens)
    post = PostRecorder((200, make_completions_body(bad_model)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_pod_rejects_markdown_fence() -> None:
    fenced_content = f"```json\n{json.dumps(VALID_MODEL_JSON)}\n```"
    post = PostRecorder((200, make_completions_body(fenced_content)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_pod_rejects_non_json_content() -> None:
    post = PostRecorder((200, make_completions_body("not a json payload")))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_pod_http_500_redacts_api_key() -> None:
    error_body = f"HTTP 500 error leaking {TEST_API_KEY} inside body".encode()
    post = PostRecorder((500, error_body))
    with pytest.raises(ConfigError) as exc_info:
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert TEST_API_KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_wish",
    [
        "",
        "   ",
        "a" * 401,
    ],
)
def test_plan_pod_rejects_invalid_wish_without_network_call(bad_wish: str) -> None:
    post = PostRecorder()
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", bad_wish, CATALOG, api_key=TEST_API_KEY, post=post)
    assert len(post.calls) == 0


@pytest.mark.parametrize(
    "bad_name",
    [
        "-bad",
        "has space",
        "a.b",
        "qwen38-27b-",
    ],
)
def test_plan_pod_rejects_invalid_name_without_network_call(bad_name: str) -> None:
    post = PostRecorder()
    with pytest.raises(ConfigError):
        plan_pod(bad_name, "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert len(post.calls) == 0


@pytest.mark.parametrize(
    "valid_name",
    [
        "qwen38-27b",
        "A",
        "ab",
    ],
)
def test_plan_pod_allows_valid_names(valid_name: str) -> None:
    post = PostRecorder()
    res = plan_pod(valid_name, "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert res["document"]["name"] == valid_name
    assert len(post.calls) == 1


def test_plan_pod_empty_api_key_rejects_without_network_call() -> None:
    post = PostRecorder()
    with pytest.raises(ConfigError) as exc_info:
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key="", post=post)
    assert "XAI_API_KEY" in str(exc_info.value)
    assert len(post.calls) == 0


def test_plan_pod_enforces_fixed_args_and_image() -> None:
    malicious_model = dict(
        VALID_MODEL_JSON,
        args="rm -rf /",
        image="evil",
    )
    post = PostRecorder((200, make_completions_body(malicious_model)))
    res = plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert res["document"]["args"] == EXPECTED_ARGS
    assert res["document"]["image"] == EXPECTED_IMAGE
    parsed = yaml.safe_load(res["yaml"])
    assert parsed["args"] == EXPECTED_ARGS
    assert parsed["image"] == EXPECTED_IMAGE


def test_plan_from_body_uses_provided_catalog_and_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", TEST_API_KEY)

    def _fail_catalog(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("gpu_catalog should not be called")

    def _fail_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("RunPodClient should not be instantiated")

    monkeypatch.setattr("podrouter.web.gpu_catalog", _fail_catalog)
    monkeypatch.setattr("podrouter.client.RunPodClient", _fail_client)

    post = PostRecorder()
    body = {"name": "qwen38-27b", "wish": "7B 모델"}
    res = plan_from_body(body, catalog=CATALOG, post=post)

    yaml_text = res["yaml"] if isinstance(res, dict) else res
    parsed = yaml.safe_load(yaml_text)
    assert parsed["args"] == EXPECTED_ARGS
    assert parsed["image"] == EXPECTED_IMAGE
    assert parsed["gpu"]["candidates"] == ["NVIDIA A40"]
    assert len(post.calls) == 1


def test_plan_from_body_rejects_missing_xai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    post = PostRecorder()
    body = {"name": "qwen38-27b", "wish": "7B 모델"}
    with pytest.raises(ConfigError) as exc_info:
        plan_from_body(body, catalog=CATALOG, post=post)
    assert "XAI_API_KEY" in str(exc_info.value)
    assert len(post.calls) == 0

    monkeypatch.setenv("XAI_API_KEY", "")
    with pytest.raises(ConfigError) as exc_info_empty:
        plan_from_body(body, catalog=CATALOG, post=post)
    assert "XAI_API_KEY" in str(exc_info_empty.value)
    assert len(post.calls) == 0


def test_plan_from_body_does_not_call_start_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", TEST_API_KEY)

    def _fail_launch(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("start_launch should not be called")

    monkeypatch.setattr("podrouter.web.start_launch", _fail_launch)

    post = PostRecorder()
    body = {"name": "qwen38-27b", "wish": "7B 모델"}
    res = plan_from_body(body, catalog=CATALOG, post=post)
    yaml_text = res["yaml"] if isinstance(res, dict) else res
    parsed = yaml.safe_load(yaml_text)
    assert parsed["args"] == EXPECTED_ARGS
    assert len(post.calls) == 1


def test_plan_pod_rejects_unoffered_tier_even_with_price() -> None:
    catalog = [
        {
            "id": "NVIDIA A40",
            "name": "NVIDIA A40",
            "memory_gb": 48,
            "secure": {"offered": False, "price": 0.5, "stock": None, "offer": None},
            "community": {"offered": True, "price": 0.4, "stock": None, "offer": None},
        }
    ]
    post = PostRecorder((200, make_completions_body(VALID_MODEL_JSON)))
    with pytest.raises(ConfigError, match="제공되지 않습니다"):
        plan_pod("qwen38-27b", "7B 모델", catalog, api_key=TEST_API_KEY, post=post)


@pytest.mark.parametrize("bad_gpu_id", ["nvidia a40", "NVIDIA A40 ", "NVIDIA A4"])
def test_plan_pod_rejects_non_exact_gpu_id(bad_gpu_id: str) -> None:
    bad_model = dict(VALID_MODEL_JSON, gpu_id=bad_gpu_id)
    post = PostRecorder((200, make_completions_body(bad_model)))
    with pytest.raises(ConfigError):
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)


def test_plan_from_body_missing_xai_key_does_not_call_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    def _fail_catalog(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("gpu_catalog should not be called")

    monkeypatch.setattr("podrouter.web.gpu_catalog", _fail_catalog)
    with pytest.raises(ConfigError, match="XAI_API_KEY"):
        plan_from_body({"name": "qwen38-27b", "wish": "7B 모델"})


def test_plan_from_body_invalid_name_does_not_call_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", TEST_API_KEY)

    def _fail_catalog(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("gpu_catalog should not be called")

    monkeypatch.setattr("podrouter.web.gpu_catalog", _fail_catalog)
    with pytest.raises(ConfigError):
        plan_from_body({"name": "-bad", "wish": "7B 모델"})


def test_plan_pod_http_error_redacts_long_body_with_key() -> None:
    error_body = ("x" * 196 + TEST_API_KEY).encode("utf-8")
    post = PostRecorder((500, error_body))
    with pytest.raises(ConfigError) as exc_info:
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert "test-xai" not in str(exc_info.value)


def test_handler_post_plan_protections(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from http.server import HTTPServer

    from podrouter.web import Handler

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _fail_launch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("start_launch should not be called")

    monkeypatch.setattr("podrouter.web.start_launch", _fail_launch)

    fake_plan = {"ok": True, "yaml": "test: ok"}
    monkeypatch.setattr("podrouter.web.plan_from_body", lambda body: fake_plan)

    base_url = f"http://127.0.0.1:{port}/api/plan"
    valid_payload = json.dumps({"name": "qwen38-27b", "wish": "7B"}).encode("utf-8")

    try:
        # 1. Content-Type text/plain 은 400
        req1 = urllib.request.Request(
            base_url,
            data=valid_payload,
            headers={"Content-Type": "text/plain", "Host": f"127.0.0.1:{port}"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc1:
            urllib.request.urlopen(req1)
        assert exc1.value.code == 400

        # 2. Origin https://evil.example 와 application/json 은 400
        req2 = urllib.request.Request(
            base_url,
            data=valid_payload,
            headers={
                "Content-Type": "application/json",
                "Host": f"127.0.0.1:{port}",
                "Origin": "https://evil.example",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc2:
            urllib.request.urlopen(req2)
        assert exc2.value.code == 400

        # 3. Host evil.example 은 400
        req3 = urllib.request.Request(
            base_url,
            data=valid_payload,
            headers={
                "Content-Type": "application/json",
                "Host": "evil.example",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc3:
            urllib.request.urlopen(req3)
        assert exc3.value.code == 400

        # 4. 올바른 Host, Content-Type application/json, plan_from_body 가짜 성공 -> 200, start_launch 안 불림
        req4 = urllib.request.Request(
            base_url,
            data=valid_payload,
            headers={
                "Content-Type": "application/json",
                "Host": f"127.0.0.1:{port}",
                "Origin": f"http://127.0.0.1:{port}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req4) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body == fake_plan
    finally:
        server.shutdown()
        server.server_close()


def test_build_plan_matches_api_plan_without_calling_xai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("subscription plan must not call xAI")

    monkeypatch.setattr("podrouter.planner.default_post", _boom)
    direct = build_plan(
        "my-pod",
        "짧은 한국어 답변",
        CATALOG,
        gpu_id="NVIDIA A40",
        cloud="SECURE",
        model="bartowski/Example-7B-GGUF:Q4_K_M",
        context_tokens=8192,
        reason="7B 공개 가중치라 A40이면 충분합니다",
    )
    via_api = plan_pod(
        "my-pod",
        "짧은 한국어 답변",
        CATALOG,
        api_key=TEST_API_KEY,
        post=PostRecorder(),
    )
    assert direct["document"] == via_api["document"]
    assert direct["yaml"] == via_api["yaml"]
    assert direct["hourly_price"] == 0.59
    path = tmp_path / "pod.yaml"
    path.write_text(direct["yaml"], encoding="utf-8")
    loaded = load_config(path)
    assert loaded.name == "my-pod"
    assert loaded.candidates == ("NVIDIA A40",)
    assert loaded.env == {}


def test_plan_document_matches_config_from_body() -> None:
    from podrouter.web import _config_from_body

    post = PostRecorder()
    res = plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    plan_doc = res["document"]

    body = {
        "name": plan_doc["name"],
        "cloud": plan_doc["gpu"]["cloud"],
        "gpu_id": plan_doc["gpu"]["candidates"][0],
        "port": plan_doc["port"],
        "disk_gb": plan_doc["storage"]["container_disk_gb"],
        "ttl": plan_doc["lifecycle"]["ttl"],
        "image": plan_doc["image"],
        "args": plan_doc["args"],
    }
    launch_doc = _config_from_body(body)["document"]
    assert plan_doc == launch_doc


def test_index_html_has_no_default_model_values() -> None:
    html_path = Path(__file__).resolve().parents[1] / "src" / "podrouter" / "static" / "index.html"
    html_text = html_path.read_text(encoding="utf-8")
    assert 'value="qwen38-27b"' not in html_text
    assert 'value="NVIDIA A40"' not in html_text
    assert "bartowski" not in html_text


def test_pod_lifecycle_html_retains_qwen_and_bartowski() -> None:
    lifecycle_path = Path(__file__).resolve().parents[1] / "docs" / "pod-lifecycle.html"
    lifecycle_text = lifecycle_path.read_text(encoding="utf-8")
    assert "qwen38-27b" in lifecycle_text
    assert "bartowski" in lifecycle_text


def test_read_plan_fields_round_trips_build_plan() -> None:
    catalog = [
        {
            "id": "NVIDIA A40",
            "secure": {"offered": True, "price": 0.44},
            "community": {"offered": False, "price": None},
        }
    ]
    result = build_plan(
        "qwen38-27b",
        "테스트",
        catalog,
        gpu_id="NVIDIA A40",
        cloud="SECURE",
        model="bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M",
        context_tokens=196608,
        reason="테스트",
    )
    assert read_plan_fields(result["yaml"]) == {
        "name": "qwen38-27b",
        "model": "bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M",
        "gpu_id": "NVIDIA A40",
        "cloud": "SECURE",
        "context_tokens": 196608,
    }


def test_read_plan_fields_reads_example_a40() -> None:
    text = (Path(__file__).resolve().parents[1] / "examples" / "a40.yaml").read_text()
    fields = read_plan_fields(text)
    assert fields["name"] == "qwen38-27b"
    assert fields["gpu_id"] == "NVIDIA A40"
    assert fields["context_tokens"] == 196608


def test_read_plan_fields_rejects_vllm_yaml() -> None:
    text = (Path(__file__).resolve().parents[1] / "examples" / "serve.yaml").read_text()
    with pytest.raises(ConfigError, match="-hf"):
        read_plan_fields(text)


def test_read_plan_fields_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError):
        read_plan_fields("- a\n- b\n")
