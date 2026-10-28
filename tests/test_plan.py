"""Unit tests for AI YAML generation contracts in podrouter.planner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from podrouter.config import load_config
from podrouter.errors import ConfigError
from podrouter.planner import build_plan, plan_pod, read_plan_fields

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
EXPECTED_ARGS = "-hf bartowski/Example-7B-GGUF:Q4_K_M --host 0.0.0.0 --port 8080 -c 8192 -ngl 99 --jinja"
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

    auth_header = call["headers"].get("Authorization") or call["headers"].get(
        "authorization"
    )
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
    bad_model = dict(
        VALID_MODEL_JSON, gpu_id="NVIDIA GeForce RTX 4090", cloud="COMMUNITY"
    )
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
        plan_pod(
            "qwen38-27b",
            "7B 모델",
            catalog_with_none_price,
            api_key=TEST_API_KEY,
            post=post,
        )


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


def test_plan_pod_http_error_redacts_long_body_with_key() -> None:
    error_body = ("x" * 196 + TEST_API_KEY).encode("utf-8")
    post = PostRecorder((500, error_body))
    with pytest.raises(ConfigError) as exc_info:
        plan_pod("qwen38-27b", "7B 모델", CATALOG, api_key=TEST_API_KEY, post=post)
    assert "test-xai" not in str(exc_info.value)


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
        "ttl": "3h",
    }


def _a40_plan(**kwargs: Any) -> dict[str, Any]:
    catalog = [
        {
            "id": "NVIDIA A40",
            "secure": {"offered": True, "price": 0.44},
            "community": {"offered": False, "price": None},
        }
    ]
    return build_plan(
        "qwen38-27b",
        "테스트",
        catalog,
        gpu_id="NVIDIA A40",
        cloud="SECURE",
        model="bartowski/Example-7B-GGUF:Q4_K_M",
        context_tokens=8192,
        reason="테스트",
        **kwargs,
    )


def test_build_plan_writes_custom_ttl(tmp_path: Path) -> None:
    result = _a40_plan(ttl=" 1h30m ")
    assert result["document"]["lifecycle"] == {"ttl": "1h30m"}
    assert read_plan_fields(result["yaml"])["ttl"] == "1h30m"
    path = tmp_path / "pod.yaml"
    path.write_text(result["yaml"], encoding="utf-8")
    assert load_config(path).ttl_sec == 5400


@pytest.mark.parametrize("bad", ["", "3", "1d", "0h", "3h; rm", 3])
def test_build_plan_rejects_bad_ttl(bad: object) -> None:
    with pytest.raises(ConfigError):
        _a40_plan(ttl=bad)


def test_read_plan_fields_defaults_ttl_when_missing() -> None:
    text = _a40_plan()["yaml"].replace("lifecycle:\n  ttl: 3h\n", "")
    assert "lifecycle" not in text
    assert read_plan_fields(text)["ttl"] == "3h"


def test_read_plan_fields_defaults_ttl_when_lifecycle_has_no_ttl() -> None:
    # lifecycle mapping exists, but ttl key is missing (legacy config form)
    text = _a40_plan()["yaml"].replace("  ttl: 3h\n", "")
    assert "lifecycle:" in text
    assert "ttl:" not in text
    assert read_plan_fields(text)["ttl"] == "3h"


@pytest.mark.parametrize(
    "bad_ttl_snippet",
    [
        "  ttl: null\n",
        "  ttl: 3600\n",
        "  ttl: 3\n",
        "  ttl: ''\n",
        '  ttl: ""\n',
        "  ttl: 0h\n",
        "  ttl: 1d\n",
        "  ttl: bad\n",
        "  ttl: '3h; rm'\n",
    ],
)
def test_read_plan_fields_rejects_existing_bad_ttl(bad_ttl_snippet: str) -> None:
    # Existing malformed/null/numeric ttl must raise ConfigError, not quietly fallback to 3h
    text = _a40_plan()["yaml"].replace("  ttl: 3h\n", bad_ttl_snippet)
    with pytest.raises(ConfigError):
        read_plan_fields(text)


def test_read_plan_fields_rejects_non_dict_lifecycle() -> None:
    text = _a40_plan()["yaml"].replace(
        "lifecycle:\n  ttl: 3h\n", "lifecycle: invalid_string\n"
    )
    with pytest.raises(ConfigError):
        read_plan_fields(text)


def test_read_plan_fields_reads_example_a40() -> None:
    text = (Path(__file__).resolve().parents[1] / "examples" / "a40.yaml").read_text()
    fields = read_plan_fields(text)
    assert fields["name"] == "qwen38-27b"
    assert fields["gpu_id"] == "NVIDIA A40"
    assert fields["context_tokens"] == 196608
    assert fields["ttl"] == "3h"


def test_read_plan_fields_rejects_vllm_yaml() -> None:
    text = (Path(__file__).resolve().parents[1] / "examples" / "serve.yaml").read_text()
    with pytest.raises(ConfigError, match="-hf"):
        read_plan_fields(text)


def test_read_plan_fields_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError):
        read_plan_fields("- a\n- b\n")


def _get_render_plan_module():
    path = (
        Path(__file__).resolve().parents[1]
        / ".grok"
        / "skills"
        / "podrouter-yaml"
        / "scripts"
        / "render_plan.py"
    )
    spec = importlib.util.spec_from_file_location("render_plan", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_plan_cli_default_ttl(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mod = _get_render_plan_module()
    monkeypatch.setattr(mod, "_catalog", lambda: CATALOG)
    out_file = tmp_path / "plan.yaml"
    rc = mod.main(
        [
            "render",
            "--name",
            "qwen-test",
            "--wish",
            "테스트",
            "--gpu-id",
            "NVIDIA A40",
            "--cloud",
            "SECURE",
            "--model",
            "bartowski/Example-7B-GGUF:Q4_K_M",
            "--context",
            "8192",
            "--reason",
            "테스트",
            "--out",
            str(out_file),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "ttl=3h" in captured.err
    loaded = read_plan_fields(out_file.read_text(encoding="utf-8"))
    assert loaded["ttl"] == "3h"


def test_render_plan_cli_custom_ttl(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mod = _get_render_plan_module()
    monkeypatch.setattr(mod, "_catalog", lambda: CATALOG)
    out_file = tmp_path / "custom.yaml"
    rc = mod.main(
        [
            "render",
            "--name",
            "qwen-custom",
            "--wish",
            "테스트",
            "--gpu-id",
            "NVIDIA A40",
            "--cloud",
            "SECURE",
            "--model",
            "bartowski/Example-7B-GGUF:Q4_K_M",
            "--context",
            "8192",
            "--ttl",
            "1h30m",
            "--reason",
            "테스트",
            "--out",
            str(out_file),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "ttl=1h30m" in captured.err
    loaded = read_plan_fields(out_file.read_text(encoding="utf-8"))
    assert loaded["ttl"] == "1h30m"


def test_render_plan_cli_rejects_bad_ttl(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _get_render_plan_module()
    monkeypatch.setattr(mod, "_catalog", lambda: CATALOG)
    rc = mod.main(
        [
            "render",
            "--name",
            "qwen-bad",
            "--wish",
            "테스트",
            "--gpu-id",
            "NVIDIA A40",
            "--cloud",
            "SECURE",
            "--model",
            "bartowski/Example-7B-GGUF:Q4_K_M",
            "--context",
            "8192",
            "--ttl",
            "0h",
            "--reason",
            "테스트",
        ]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "ttl" in captured.err


def test_render_plan_inspect_cli(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    mod = _get_render_plan_module()
    plan_file = tmp_path / "inspect_target.yaml"
    plan_file.write_text(_a40_plan(ttl="45m")["yaml"], encoding="utf-8")
    rc = mod.main(["inspect", "--file", str(plan_file)])
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ttl"] == "45m"
    assert output["name"] == "qwen38-27b"


def test_render_plan_inspect_cli_rejects_bad_ttl(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    mod = _get_render_plan_module()
    plan_file = tmp_path / "bad_target.yaml"
    bad_yaml = _a40_plan()["yaml"].replace("  ttl: 3h\n", "  ttl: 3600\n")
    plan_file.write_text(bad_yaml, encoding="utf-8")
    rc = mod.main(["inspect", "--file", str(plan_file)])
    assert rc == 1
    assert "ttl" in capsys.readouterr().err
