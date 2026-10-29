---
name: podrouter-pod
description: >
  podrouter Mac 앱의 Pod 설정(YAML)을 대화로 만든다. 사용자가 원하는 용도를 말하거나
  모델 카드, 메모 같은 문서를 붙여넣으면 GPU, 공개 GGUF 모델, 문맥 길이를 골라
  앱의 저장 폴더에 YAML을 쓴다. 앱은 그 파일을 자동으로 불러온다. /podrouter-pod 를
  실행하거나 "Pod 설정 만들어줘", "이 문서대로 Pod 설정해줘"라고 할 때 사용한다.
---

# podrouter Pod 설정 만들기

이 스킬은 Pod 설정 YAML 파일만 만든다. Pod를 켜거나 끄지 않는다. 모든 답은 한국어로 한다.

## 실행기

앱이 열어 준 작업 폴더(설치형은 `~/podrouter`, 개발용은 git 저장소)에서 아래 실행기만 쓴다. 실행기는 앱이 만들어 두며, 그 폴더의 `.env`에서 Runpod 키를 읽는다. 에이전트는 `.env`를 직접 읽지 않는다.

```bash
.podrouter/pod-plan <catalog|render|inspect|guide> ...
```

`.podrouter/pod-plan`이 없으면 podrouter 앱을 한 번 열었다 다시 시도하라고 안내하고 멈춘다.
`RUNPOD_API_KEY가 없습니다`가 나오면 앱의 Keys 화면에서 키를 저장하라고 안내하고 멈춘다.

## 1. 입력 받기

- 사용자가 문서(모델 카드, 메모, 요구사항)를 붙여넣었으면 거기서 이름, 용도, 원하는 모델, GPU, 문맥 길이, 수명(TTL), 예산 단서를 읽는다.
- 이름이나 용도를 알 수 없으면 그 둘만 한 번 묻는다.
- 수명(TTL)은 사용자가 언급하지 않으면 기본 3시간(`3h`)으로 설정한다. 사용자가 원하면 `30m`, `1h30m`처럼 시간(h)과 분(m) 형식으로 지정할 수 있다.
- 이름은 영문, 숫자, 하이픈만 쓰고 41자 이하로 한다. `.env`의 `PODROUTER_PROTECTED_PODS`에 있는 이름은 거절한다(앱의 키 관리 화면에서 보인다).
- 사용자가 기존 YAML 파일 경로를 주면 `inspect --file <경로>`로 읽어 출발점으로 삼는다.

## 2. 고르기

먼저 `.podrouter/pod-plan guide`로 고르는 규칙을 읽는다. 그다음 카탈로그를 본다.

```bash
.podrouter/pod-plan catalog
```

- `gpu_id`는 카탈로그의 `id`를 그대로 쓴다. `offered`가 참이고 `price`가 숫자인 클라우드만 고른다.
- 모델은 공개 Hugging Face GGUF를 우선한다. 형식은 `owner/repo` 또는 `owner/repo:QUANT`이다. 고른 저장소와 양자화 파일이 실제로 있는지 확인한다.
- 사용자가 승인이 필요한(게이트된) 모델을 원하면 쓸 수는 있다. 다만 앱 Keys 화면에서 `HF_TOKEN`을 저장해야 하고, 그 계정으로 Hugging Face에서 이용 동의를 해야 한다고 알린다. YAML에는 `env`나 `HF_TOKEN`을 쓰지 않는다. 앱이 `.env`의 토큰을 Pod에 넘긴다.
- GPU 메모리에 맞는 양자화와 문맥 길이를 고른다. 문맥 길이는 2048 이상 262144 이하의 정수다.
- 고른 결과(GPU, 시간당 가격, 모델, 문맥 길이, 수명 TTL, 이유)를 짧게 보여주고 저장해도 되는지 묻는다.

## 3. 저장

사용자가 좋다고 하면 앱 저장 폴더에 쓴다. 파일 이름은 Pod 이름과 같게 한다.

```bash
.podrouter/pod-plan render \
  --name "<이름>" \
  --wish "<용도>" \
  --gpu-id "<카탈로그 id>" \
  --cloud SECURE \
  --model "<owner/repo:QUANT>" \
  --context <정수> \
  --ttl "<3h|30m|1h30m>" \
  --reason "<한국어 한 문장>" \
  --out "pods/<이름>.yaml"
```

- `--ttl`은 Pod 수명이며 생략 시 기본 `3h`입니다 (`30m`, `1h30m` 형식 지원).
- 사용자가 llama-server 옵션(예: 생각 과정 끄기 `--reasoning-budget 0`)을 원하면 `--extra-args="--reasoning-budget 0"`처럼 `=` 형태로 붙인다. 따옴표, `; $ ` | & < > ( ) # * ? [ ] { } ~ \`, 그리고 `-hf -c --ctx-size --host --port -ngl --jinja`는 쓸 수 없다.
- 앱의 예약 스케줄(타이머)로 켜지는 경우, 설정된 종료 시각(+15분)에 맞춰 계산된 남은 수명이 YAML의 TTL을 override합니다.

실패하면 오류 문구를 그대로 전하고 값을 고쳐 다시 렌더한다. 성공하면 표준 오류의 `hourly_price`를 함께 알리고, 이렇게 안내한다.

> podrouter 앱으로 돌아가면 이 설정이 Pod 화면에 자동으로 채워집니다.

## 하지 않는 일

- `podrouter up`, `podrouter down`, Pod 생성과 종료.
- `.env`, `.env.*` 읽기. 키를 직접 꺼내 출력하기. 키 값을 대화에 쓰기.
- 사용자가 키를 대화에 붙여넣으면 다시 쓰지 말고, 그 키를 폐기하고 앱의 Keys 화면에 새로 넣으라고 안내한다.
- `examples/a40.yaml` 덮어쓰기.
