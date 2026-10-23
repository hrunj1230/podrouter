"""xAI-powered planner for podrouter YAML configurations."""

from __future__ import annotations

import json
import re
import shlex
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import yaml

from podrouter.errors import ConfigError

_NAME_REGEX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,39}[A-Za-z0-9])?$")
_MODEL_REGEX = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{1,80}/[A-Za-z0-9][A-Za-z0-9._-]{1,120}"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{1,32})?$"
)
_FORBIDDEN_MODEL_CHARS = ('"', "'", " ", ";", "$", "`", "\\", "\n", "\r")

_SYSTEM_PROMPT = (
    "당신은 GPU 및 LLM 모델 추천 전문가입니다. 사용자의 요청(Pod 이름과 용도)과 제공된 GPU 카탈로그를 분석하여 "
    "최적의 GPU와 Hugging Face GGUF 모델을 선택해야 합니다.\n\n"
    "지침:\n"
    "1. 반드시 공개 Hugging Face GGUF 가중치만 선택하십시오. 형식은 owner/repo 또는 owner/repo:QUANT 이어야 합니다 "
    "(llama.cpp -hf 로 받는 가중치).\n"
    "2. gpu_id는 카탈로그에 있는 gpu_id를 그대로 복사하여 사용하십시오.\n"
    "3. 선택한 GPU의 VRAM(memory_gb)에 맞는 양자화를 선택하십시오.\n"
    "4. cloud는 SECURE 또는 COMMUNITY 중 하나를 선택하십시오. 재고가 있으면 그 클라우드를 우선하고, "
    "재고가 없어도 가격이 숫자인 클라우드를 고를 수 있습니다.\n"
    "5. context_tokens는 2048 이상 262144 이하의 정수로 지정하십시오.\n"
    "6. 셸, 이미지, 환경변수는 반환하지 마십시오.\n"
    "7. reason은 선택한 이유를 설명하는 한국어 한 문장으로 작성하십시오."
)

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "podrouter_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "gpu_id": {"type": "string"},
                "cloud": {"type": "string", "enum": ["SECURE", "COMMUNITY"]},
                "model": {"type": "string"},
                "context_tokens": {"type": "integer", "minimum": 2048, "maximum": 262144},
                "reason": {"type": "string"},
            },
            "required": ["gpu_id", "cloud", "model", "context_tokens", "reason"],
        },
    },
}


def compact_catalog_rows(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep id, memory, offered, price, and stock. Drop every other field."""
    compact: list[dict[str, Any]] = []
    for row in catalog:
        if not isinstance(row, dict):
            continue
        sec = row.get("secure") if isinstance(row.get("secure"), dict) else {}
        comm = row.get("community") if isinstance(row.get("community"), dict) else {}
        compact.append(
            {
                "id": row.get("id"),
                "memory_gb": row.get("memory_gb"),
                "secure": {
                    "offered": bool(sec.get("offered")),
                    "price": sec.get("price"),
                    "stock": sec.get("stock"),
                },
                "community": {
                    "offered": bool(comm.get("offered")),
                    "price": comm.get("price"),
                    "stock": comm.get("stock"),
                },
            }
        )
    return compact


def validate_plan_input(name: str, wish: str) -> str:
    """Validate Pod name and wish text before catalog or LLM calls."""
    if not isinstance(name, str) or _NAME_REGEX.fullmatch(name) is None:
        raise ConfigError("Pod 이름은 영문, 숫자, 하이픈으로 구성된 1~41자여야 하며 하이픈으로 끝날 수 없습니다.")
    if not isinstance(wish, str):
        raise ConfigError("용도는 문자열이어야 합니다.")
    if len(wish) > 400:
        raise ConfigError("용도는 400자 이하이어야 합니다.")
    wish_folded = re.sub(r"[\r\n]+", " ", wish).strip()
    if not (1 <= len(wish_folded) <= 400):
        raise ConfigError("용도는 1자 이상 400자 이하이어야 합니다.")
    return wish_folded


def _redact_text(text: str, api_key: str = "") -> str:
    try:
        from podrouter.web import _secrets

        secrets = _secrets()
    except (ImportError, AttributeError):
        secrets = []
    if api_key and api_key not in secrets:
        secrets.append(api_key)
    secrets.sort(key=len, reverse=True)
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer ***", redacted)
    return redacted


def default_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float = 45,
) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        safe_msg = _redact_text(str(exc))
        raise ConfigError(f"xAI API 연결 실패: {safe_msg}") from exc


def plan_pod(
    name: str,
    wish: str,
    catalog: list[dict[str, Any]],
    *,
    api_key: str,
    post: Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, bytes]] | None = None,
) -> dict[str, Any]:
    wish_folded = validate_plan_input(name, wish)

    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("XAI_API_KEY가 비어 있습니다.")

    if not isinstance(catalog, list):
        raise ConfigError("GPU 카탈로그가 올바르지 않습니다.")

    compact_catalog = compact_catalog_rows(catalog)

    user_content = (
        f"이름: {name}\n"
        f"용도: {wish_folded}\n"
        f"카탈로그: {json.dumps(compact_catalog, ensure_ascii=False, separators=(',', ':'))}"
    )

    payload = {
        "model": "grok-4.7",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": _RESPONSE_FORMAT,
    }

    post_fn = post or default_post
    status, body_bytes = post_fn(
        "https://api.x.ai/v1/chat/completions",
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        payload,
        45,
    )

    if status != 200:
        raw_text = body_bytes.decode("utf-8", errors="replace")
        safe_body = _redact_text(raw_text, api_key)[:200]
        raise ConfigError(f"xAI API 호출 실패 (HTTP {status}): {safe_body}")

    try:
        resp_obj = json.loads(body_bytes.decode("utf-8"))
        content = resp_obj["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError("xAI API 응답 형식이 올바르지 않습니다.") from exc

    if not isinstance(content, str):
        raise ConfigError("모델 응답 내용이 올바르지 않습니다.")

    if "```" in content:
        raise ConfigError("모델 응답에 마크다운 펜스가 포함되어 있습니다.")

    content_trimmed = content.strip()
    if not (content_trimmed.startswith("{") and content_trimmed.endswith("}")):
        raise ConfigError("모델 응답 앞뒤에 불필요한 설명이 포함되어 있거나 올바른 JSON이 아닙니다.")

    try:
        parsed = json.loads(content_trimmed)
    except json.JSONDecodeError as exc:
        raise ConfigError("잘린 JSON이거나 파싱할 수 없는 모델 응답입니다.") from exc

    if not isinstance(parsed, dict):
        raise ConfigError("모델 응답은 JSON 객체여야 합니다.")

    for req_key in ("gpu_id", "cloud", "model", "context_tokens", "reason"):
        if req_key not in parsed:
            raise ConfigError(f"모델 응답에 필수 항목({req_key})이 누락되었습니다.")

    return build_plan(
        name,
        wish,
        catalog,
        gpu_id=parsed.get("gpu_id"),
        cloud=parsed.get("cloud"),
        model=parsed.get("model"),
        context_tokens=parsed.get("context_tokens"),
        reason=parsed.get("reason"),
        api_key=api_key,
    )


def build_plan(
    name: str,
    wish: str,
    catalog: list[dict[str, Any]],
    *,
    gpu_id: object,
    cloud: object,
    model: object,
    context_tokens: object,
    reason: object,
    api_key: str = "",
) -> dict[str, Any]:
    """Validate a chosen GPU and public GGUF, then render the podrouter YAML.

    The subscription skill calls this directly. It does not contact xAI.
    """
    validate_plan_input(name, wish)
    if not isinstance(catalog, list):
        raise ConfigError("GPU 카탈로그가 올바르지 않습니다.")

    if not isinstance(gpu_id, str):
        raise ConfigError("gpu_id는 문자열이어야 합니다.")

    matched_row: dict[str, Any] | None = None
    for row in catalog:
        if isinstance(row, dict) and row.get("id") == gpu_id:
            matched_row = row
            break

    if matched_row is None:
        raise ConfigError(f"카탈로그에 없는 GPU id입니다: {gpu_id}")

    if cloud not in ("SECURE", "COMMUNITY"):
        raise ConfigError("cloud는 SECURE 또는 COMMUNITY여야 합니다.")

    tier = matched_row.get("secure" if cloud == "SECURE" else "community")
    if not isinstance(tier, dict) or tier.get("offered") is not True:
        raise ConfigError(f"{gpu_id}는 {cloud} 클라우드에서 제공되지 않습니다.")

    price = tier.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise ConfigError(f"{gpu_id}의 {cloud} 클라우드 가격 정보가 올바르지 않습니다.")
    hourly_price = price

    if not isinstance(model, str) or _MODEL_REGEX.fullmatch(model) is None:
        raise ConfigError(f"유효하지 않은 모델 형식입니다: {model}")
    if any(ch in model for ch in _FORBIDDEN_MODEL_CHARS):
        raise ConfigError(f"모델 이름에 허용되지 않는 문자가 포함되어 있습니다: {model}")

    if isinstance(context_tokens, bool) or not isinstance(context_tokens, int):
        raise ConfigError("context_tokens는 정수여야 합니다.")
    if not (2048 <= context_tokens <= 262144):
        raise ConfigError("context_tokens는 2048 이상 262144 이하이어야 합니다.")

    if not isinstance(reason, str) or not (1 <= len(reason) <= 400):
        raise ConfigError("reason은 1자 이상 400자 이하의 문자열이어야 합니다.")
    reason = _redact_text(reason, api_key)

    document = {
        "name": name,
        "image": "ghcr.io/ggml-org/llama.cpp:server-cuda13",
        "args": f"-hf {model} --host 0.0.0.0 --port 8080 -c {context_tokens} -ngl 99 --jinja",
        "gpu": {
            "candidates": [gpu_id],
            "count": 1,
            "cloud": cloud,
        },
        "storage": {
            "container_disk_gb": 60,
            "network_volume_id": None,
        },
        "port": 8080,
        "readiness": {
            "path": "/health",
            "interval_sec": 10,
            "timeout_sec": 900,
        },
        "lifecycle": {
            "ttl": "3h",
        },
        "timeouts": {
            "allocation_sec": 180,
            "boot_sec": 600,
            "boot_interval_sec": 5,
        },
    }

    yaml_text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    return {
        "yaml": yaml_text,
        "document": document,
        "gpu_id": gpu_id,
        "cloud": cloud,
        "model": model,
        "context_tokens": context_tokens,
        "reason": reason,
        "hourly_price": hourly_price,
    }


def read_plan_fields(text: str) -> dict[str, Any]:
    """Read the app's form fields back from a llama.cpp podrouter YAML.

    Used to reload saved or imported YAML into the Mac app. Secrets in the
    YAML are never resolved here.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigError("YAML 최상위가 매핑이 아닙니다.")

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("name이 없습니다.")

    args = doc.get("args")
    if not isinstance(args, str):
        raise ConfigError("args가 없습니다.")
    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        raise ConfigError(f"args를 해석할 수 없습니다: {exc}") from exc

    model: str | None = None
    context: int | None = None
    for idx, token in enumerate(tokens[:-1]):
        nxt = tokens[idx + 1]
        if token in ("-hf", "--hf-repo"):
            model = nxt
        elif token in ("-c", "--ctx-size"):
            try:
                context = int(nxt)
            except ValueError as exc:
                raise ConfigError(f"문맥 길이가 정수가 아닙니다: {nxt}") from exc
    if model is None:
        raise ConfigError(
            "llama.cpp `-hf` 모델이 없는 YAML입니다. 앱은 공개 GGUF YAML만 불러옵니다."
        )

    gpu = doc.get("gpu")
    if not isinstance(gpu, dict):
        raise ConfigError("gpu 항목이 없습니다.")
    candidates = gpu.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], str):
        raise ConfigError("gpu.candidates가 비어 있습니다.")
    cloud = gpu.get("cloud", "SECURE")
    if cloud not in ("SECURE", "COMMUNITY"):
        raise ConfigError("이 앱은 SECURE 또는 COMMUNITY 클라우드만 다룹니다.")

    return {
        "name": name.strip(),
        "model": model,
        "gpu_id": candidates[0],
        "cloud": cloud,
        "context_tokens": context if context is not None else 8192,
    }
