# podrouter 사용자 설정

사람이 계정, 비밀값, Tailscale 콘솔, 서버 스펙, 호출 코드에서 맞추는 항목만 모았다. 코드가 기본으로 채우는 값과, 넣어 두어도 지금 Pod에는 전달되지 않는 값은 따로 표시한다.

현재 서버 스펙의 기준 파일은 `examples/a40.yaml`이다.

비밀값은 이 문서에 적지 않는다. `.env`는 저장소 루트에 있고 권한은 `600`이며 git에 포함되지 않는다.

## 1. 로컬에서 넣을 비밀값

맥 앱과 타이머는 명령을 실행할 때마다 `.env`를 읽는다. 이미 프로세스에 있는 값은 덮어쓰지 않는다. `podrouter` CLI는 `.env`를 자동으로 읽지 않으므로, CLI로 켤 때는 같은 값이 셸 환경에 있어야 한다.

| 이름 | 지금 필요한가 | 넣는 곳 | 하는 일 |
| --- | --- | --- | --- |
| `RUNPOD_API_KEY` | 필수 | `.env` | Runpod Pod 조회, 생성, 종료. 없으면 맥 앱의 켜기와 끄기가 거절된다. Pod 설정 스킬도 이 키로 GPU 카탈로그를 조회한다. |
| `PODROUTER_TUNNEL` | 선택 | `.env` | 공개 주소 방식. `none`, `tailscale`, `cloudflare` 중 하나다. 맥 앱 Keys 화면의 "공개 주소 방식"에서 고르면 저장된다. 비어 있으면 예전처럼 `TS_AUTHKEY`가 있을 때 Tailscale, 없을 때 `none`이다. 비밀값이 아니다. |
| `TS_AUTHKEY` | `tailscale` 방식이면 필수 | `.env` | 선택된 방식이 `tailscale`일 때만 Pod 환경변수로 전달되고, Pod 안에서 Tailscale Funnel이 켜진다. |
| `CF_TUNNEL_TOKEN` | 선택 | `.env` | `cloudflare` 방식에서 값이 있으면 Named Tunnel(고정 주소), 없으면 Quick Tunnel(`https://<임의>.trycloudflare.com`, 켤 때마다 바뀜)이다. Pod 안에서는 `TUNNEL_TOKEN` 환경변수로만 쓰여 명령줄에 나오지 않는다. |
| `CF_TUNNEL_HOSTNAME` | 선택 | `.env` | Named Tunnel의 공개 주소(예: `llm.example.com`). 표시용이며 비밀값이 아니다. 실제 연결은 Cloudflare 대시보드의 Public Hostname 설정이 정한다. |
| `HF_TOKEN` | 선택 | `.env` | 값이 있으면 Pod 환경변수로 전달된다. 모델 다운로드가 권한 오류로 실패하거나 게이트된 모델을 쓸 때 넣는다. |

키는 모두 저장소 `.env` 한 곳에 둔다. podrouter 맥 앱의 Keys 화면에서 저장하면 해당 줄만 바뀌고, 다른 줄은 그대로 남으며, 파일 권한은 0600(나만 읽기)으로 맞춰진다. 맥 타이머는 `schedule tick --env-file <저장소>/.env`로 같은 파일을 읽는다. Claude는 설정상 `.env`를 읽을 수 없다.

`TS_AUTHKEY`는 Tailscale 관리 화면의 Auth key다. 지금은 아래처럼 만들어 두었다.

- Reusable: 켜짐. 같은 키로 Pod를 다시 만들 수 있다.
- Ephemeral: 켜짐. Pod가 사라진 뒤 노드는 Tailscale이 지운다. 이 속성은 키에 붙는 것이고, `tailscale up` 명령의 플래그가 아니다.
- Tags: 비움. 태그가 있으면 기본 Funnel 허용 대상에서 빠진다.
- 값 형식: `tskey-auth-`로 시작한다. 따옴표와 앞뒤 공백 없이 한 줄이다.
- 만료: 만들 때 지정한 일수이며 최대 90일이다. 만료되면 맥 앱의 키 관리에서 새 키를 저장한다.

Cloudflare Named Tunnel은 Cloudflare Zero Trust 대시보드의 Networks → Tunnels에서 만든다. Public Hostname의 서비스는 `http://localhost:<YAML의 port>`로 둔다. 설치 명령에 나오는 토큰(`eyJ`로 시작)을 `CF_TUNNEL_TOKEN`으로 저장한다. 도메인이 Cloudflare DNS에 있어야 한다. Cloudflare 프록시는 첫 응답 바이트를 약 100초까지 기다리므로, 긴 답변은 스트리밍으로 받는다.

방식을 바꾸면 config hash 뒤에 붙는 표시(`-funnel`, `-cloudflare`, `-cloudflare-quick`)가 달라져서, 이미 켜진 Pod를 재사용하지 않고 새로 만든다.

## 2. Tailscale 콘솔에서 한 번 할 일

맥에는 Tailscale을 설치하지 않는다. `tailscale up`도 맥에서 실행하지 않는다. 기기는 Pod가 스스로 등록한다.

| 항목 | 상태 | 사람이 할 일 |
| --- | --- | --- |
| Auth key | 발급됨 | 위 조건이 유지되는지 본다. 태그를 나중에 붙이지 않는다. |
| Funnel 허용 | 이 테일넷에서 이미 승인됨 | 새 테일넷이면 Pod 로그의 `https://login.tailscale.com/f/funnel?node=...`를 테일넷 소유 계정으로 한 번 연다. Pod마다 반복하지 않는다. |
| MagicDNS | 켜 둔 상태 유지 | 끄면 Funnel 주소를 쓰지 못한다. |
| HTTPS 인증서 | 켜 둔 상태 유지 | 끄면 Funnel 주소를 쓰지 못한다. |
| 머신 이름 | Pod 이름과 같음 | 관리 화면에서 이름을 바꾸면 호출 주소가 바뀐다. |

호출에 쓰는 공개 주소는 `https://<pod이름>.<tailnet>.ts.net`이다. 이 호스트명은 Tailscale이 Pod 로그에 출력한 값이다. 경로 `/v1`을 붙이고, `:8080`은 붙이지 않는다. Funnel의 공개 포트는 443이다.

공개 DNS는 Funnel이 시작된 뒤 최대 10분이 걸릴 수 있다. 그 전에는 이 이름이 조회되지 않을 수 있다. 직전 Pod가 아직 테일넷에 남아 있으면 새 Pod는 `qwen38-27b-1`이 될 수 있고, 그 이름은 자동으로 되돌아가지 않는다. Ephemeral 노드는 마지막 활동 후 30분에서 48시간 사이에 지워진다.

## 3. 서버 스펙

아래는 `examples/a40.yaml`의 값이다. 이미지 기본값은 `ghcr.io/ggml-org/llama.cpp:server-cuda13`이다.

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 이름 | `qwen38-27b` | Tailscale 호스트명이 된다. 영문, 숫자, 하이픈만 허용된다. |
| 클라우드 | `SECURE` | 화면에서 맞춘 A40 가격이 Secure Cloud 가격이었다. |
| GPU | `NVIDIA A40` 1개 | 시간당 가격 상한으로 생성을 막지 않으며, 생성·재사용 시 해당 Pod의 시간당 GPU 비용(디스크 요금 제외)이 결과에 출력된다. |
| 이미지 | `ghcr.io/ggml-org/llama.cpp:server-cuda13` | CUDA 13 llama.cpp 서버. |
| 인자 | `-hf bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M --host 0.0.0.0 --port 8080 -c 196608 -ngl 99 --jinja` | 공개 Q4_K_M 가중치. 문맥 길이는 192×1024다. |
| 포트 | `8080` | 컨테이너 안 llama-server. Funnel이 공개 443을 이 포트로 보낸다. |
| 디스크 | 컨테이너 디스크 60GB | 네트워크 볼륨은 없다. |
| TTL | `3h` | 이 시간이 지나면 종료를 시도한다. |
| 준비 확인 | `/health` | 모델 적재 전에는 503이고, 끝나면 200이다. |
| 켜기 재시도 | 15초 간격(`timeouts.allocation_retry_sec`) | 자리가 없으면 `timeouts.allocation_sec` 동안 그 설정으로 다시 만든다. |
| 헬스 대기 | 맥 앱과 타이머는 `--wait false` | 헬스 실패가 Pod를 지우고 다시 만드는 일을 막는다. |

이름을 바꾸면 호출 주소의 호스트명도 바뀐다. 이미지, 인자, GPU, 디스크, 포트를 바꾸면 설정 해시가 바뀐다. 이전 Pod가 아직 RUNNING이면 켜기만 눌러도 청구되는 Pod가 하나 더 생긴다. 스펙을 바꾼 뒤에는 끄기를 먼저 한다.

`lifecycle.idle_timeout: 30m`은 파일에 있지만 동작에는 쓰이지 않는다. 컨테이너 시작 명령에 넣는 3시간 뒤 `runpodctl remove`는 이 이미지에 `runpodctl`이 없으면 실행되지 않는다. 끌 때는 맥 앱의 끄기 또는 `podrouter down qwen38-27b`를 쓴다.

## 4. 모델을 호출하는 코드

고정할 주소는 아래다.

```text
https://<pod이름>.<tailnet>.ts.net/v1
```

이 llama.cpp 스펙에는 API 키가 없다. OpenAI SDK를 쓰면 `api_key`에 `not-used` 같은 임의 문자열을 넣는다. 서버는 그 값을 검사하지 않는다.

짧은 답변이 비어 있으면 토큰이 생각 문장으로 들어간 경우다. 요청에 다음을 넣으면 본문이 `content`로 온다.

```json
{ "chat_template_kwargs": { "enable_thinking": false } }
```

준비 확인은 `GET /health`이고, 모델 목록은 `GET /v1/models`이다. 공개 DNS가 아직 없으면 그 사이의 확인만 Runpod 프록시 `https://<pod-id>-8080.proxy.runpod.net`을 쓴다. 이 호스트는 Pod를 다시 만들 때마다 바뀌므로 호출 코드에 고정하지 않는다.

## 5. 넣어 두었지만 지금 서버에는 안 넘어가는 값

| 이름 | 상태 |
| --- | --- |
| `HF_TOKEN` | 선택이다. `podrouter up`을 실행하는 환경에 값이 있으면 Pod 환경변수 `HF_TOKEN`으로 넘기고, 없으면 넘기지 않는다. 맥 앱과 타이머 모두 데이터 폴더 `.env`에서 읽는다. 로그에서는 가려진다. 지금 가중치는 공개 저장소라 키가 없어도 받는다. |
| `VLLM_API_KEY` | 예전 vLLM 예시용이다. 지금 llama.cpp 스펙은 읽지 않는다. |
| `CLOUDFLARE_API_TOKEN` | 사용하지 않는다. `example.com` DNS는 수정하지 않는다. |

게이트된 `orcarouter/Qwen3.8-27B-Uncensored-GGUF`로 되돌리려면 그 계정으로 모델 이용 동의를 한 뒤 `HF_TOKEN`을 저장한다. 동의하지 않은 계정의 토큰으로는 받아지지 않는다.

## 6. 건드리지 않을 것

- 이 도구가 만들지 않은 Pod. `PODROUTER_PROTECTED_PODS`에 적은 이름은 켜기, 끄기, 전체 종료, 예약에서도 막는다.
- 맥의 Tailscale 로그인.
- Cloudflare DNS와 `example.com`.
- Runpod 화면의 Deploy Pod. 생성은 이 도구의 켜기만 쓴다.
- 태그 있는 Tailscale 키.
- 네트워크 볼륨. 템플릿 저장 경로 `/workspace`에는 아무것도 마운트되어 있지 않다. 볼륨이 필요하면 따로 요청한다.
