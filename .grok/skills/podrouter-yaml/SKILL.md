---
name: podrouter-yaml
description: >
  podrouter Pod YAML을 이 세션의 구독 에이전트가 직접 고르게 한다. xAI API와
  XAI_API_KEY는 쓰지 않고 Pod도 만들지 않는다. 사용자가 YAML 초안, 구독형
  AI로 YAML, Pod 이름과 용도로 설정 파일을 만들어 달라고 하거나 /podrouter-yaml
  을 실행할 때 사용한다.
---

# podrouter YAML (구독 에이전트)

이 스킬을 읽는 에이전트가 모델과 GPU를 고른다. `api.x.ai`를 호출하지 않는다. `XAI_API_KEY`를 읽거나 요구하지 않는다. 이 선택만을 위해 Orca 워커를 띄우지 않는다.

YAML 본문과 검증 문구의 기준은 `src/podrouter/planner.py`의 `_SYSTEM_PROMPT`와 `build_plan`이다. 고르기 전에 그 둘을 읽는다. 이미지, 포트는 스크립트가 넣는다. 에이전트는 이름, 용도, GPU, 클라우드, 공개 GGUF, 문맥 길이, 이유를 정하며, 사용자가 요청하면 수명(TTL, 기본 `3h`, `30m`/`1h30m` 형식)도 `--ttl`로 함께 전달한다.

## 입력

이름과 용도가 없으면 그 두 가지만 한 번 묻는다. 규칙 문장은 `validate_plan_input`이 실패할 때 그대로 전한다.

## 카탈로그

저장소 루트에서 실행한다.

```bash
.venv/bin/python .grok/skills/podrouter-yaml/scripts/render_plan.py catalog
```

`RUNPOD_API_KEY`가 없으면 멈추고 사용자에게 알린다. 키 값은 출력하지 않는다. `gpu_id`는 이 JSON의 `id`를 그대로 쓴다. `offered`가 참이고 `price`가 숫자인 클라우드만 고른다.

## 선택

공개 Hugging Face GGUF만 고른다. 형식은 `owner/repo` 또는 `owner/repo:QUANT`이다. 게이트된 저장소와 `HF_TOKEN`은 넣지 않는다. 서버는 파일의 존재까지 확인하지 않으므로, 고른 문자열이 공개 저장소의 실제 파일인지도 확인한다. 확인이 안 되면 그 모델을 쓰지 않고 사용자에게 말한다.

`context_tokens`는 2048 이상 262144 이하인 정수다. `reason`은 한국어 한 문장이고 400자 이하다.

## 렌더

```bash
.venv/bin/python .grok/skills/podrouter-yaml/scripts/render_plan.py render \
  --name "<이름>" \
  --wish "<용도>" \
  --gpu-id "<카탈로그 id>" \
  --cloud SECURE \
  --model "<owner/repo 또는 owner/repo:QUANT>" \
  --context <정수> \
  --reason "<한 문장>"
```

수명(TTL)은 기본 `3h`이며, 사용자가 원하면 `--ttl "<3h|30m|1h30m>"`으로 전달한다. 앱 예약 스케줄로 켤 때는 종료 시각(+15분) 기준 계산된 남은 TTL이 YAML TTL을 override한다.

표준 출력이 YAML이다. 표준 오류의 `hourly_price`를 사용자에게 함께 말한다. 실패 문구는 그대로 전하고, 값을 바꿔 다시 렌더한다.

사용자가 저장 경로를 말한 경우에만 `--out`을 붙인다. `.env`, `examples/a40.yaml`은 스크립트가 거절한다. 경로가 없으면 답변에 YAML을 보여주고 파일은 쓰지 않는다.

## 하지 않는 일

- Pod 생성, `podrouter up`. 사용자가 띄우라고 한 뒤에만 한다.
- `env`, `HF_TOKEN`, `max_price_per_hour`, `idle_timeout`을 YAML에 넣기.
