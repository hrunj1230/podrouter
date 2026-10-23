# ADR-0001: Pod를 다시 만들어도 호출 주소를 고정한다

## Status

Accepted

## Context

podrouter은 Runpod Pod를 만들고, 끝나면 그 Pod를 종료한다. 예약 인스턴스로 남겨 두지 않는다. Runpod는 Pod용 예약 고정 공인 IP를 제공하지 않는다. 공식 프록시 주소는 `https://<pod-id>-<port>.proxy.runpod.net`이라 Pod를 지우고 다시 만들면 호스트가 바뀐다. 이 형식은 Runpod 포트 공개 문서와 `src/podrouter/urls.py`가 같다.

호출 코드에는 `base_url`을 한 번만 적고 싶다. 맥에는 Tailscale을 설치하지 않고, 등록은 Pod 안에서 한다.

이 기록은 그 선택을 정리한 것이다. Tailscale Funnel을 쓰자는 지시는 이 비교보다 먼저 있었다. 당시 안은 도메인을 사지 않고, 본인 것이 아닌 `example.com` DNS도 수정하지 않으며, 바뀌는 프록시 대신 고정 주소를 쓰자는 것이었다. Pod 중지와 마이그레이션의 주소 변화, Funnel의 DNS 지연과 ephemeral 삭제 시간은 이 ADR을 쓰면서 Runpod·Tailscale 문서로 확인했다.

현재 서버는 `examples/a40.yaml`의 `qwen38-27b`이다. 컨테이너 포트는 8080이고 네트워크 볼륨은 없다. `TS_AUTHKEY`가 있으면 `src/podrouter/funnel.py`가 컨테이너 시작 명령을 바꿔, Pod가 사용공간 네트워킹으로 Tailscale에 올라간 뒤 공개 443을 그 포트로 연다.

이전에 확인한 Cloudflare 계정에는 zone이 없었다. 도메인을 만들지 않는다는 조건은 이번 결정의 필수 기준으로 다시 넣지 않았다. zone이 없는 상태가 계속되면 Cloudflare Named Tunnel은 지금 쓸 수 없다.

## Decision Drivers

필수 조건은 세 가지다.

- 호출 코드에 주소를 고정할 수 있어야 한다. Runpod 프록시 호스트는 Pod ID에 묶여 재생성마다 바뀐다.
- 맥에는 Tailscale을 설치하지 않는다. 등록은 Pod 안에서 한다.
- Pod는 예약해 두지 않고 종료한다. Runpod에 예약 고정 공인 IP는 없다.

지연 시간의 수치 목표는 정하지 않았다. Tailscale이 적은 공개 DNS 최대 10분은 제품 제한으로 기록만 한다.

## Considered Options

### 1. Pod 안에서 Tailscale Funnel

`TS_AUTHKEY`를 Pod 환경변수로 넘긴다. 컨테이너가 `tailscale up --hostname=<파드 이름>`으로 자신을 등록하고, `tailscale funnel`이 공개 443을 `127.0.0.1:8080`으로 보낸다. 호출자는 `https://qwen38-27b.<테일넷>.ts.net/v1`을 고정한다. 주소에 `:8080`은 붙이지 않는다. 맥은 tailnet에 들어오지 않는다.

**장점**

- 종료 뒤 다시 만들어도 같은 호스트명을 다시 등록할 수 있다.
- 등록 주체가 Pod라 맥용 Tailscale이 필요 없다.
- Funnel은 모든 요금제에서 사용할 수 있고, 수신 포트 443으로 HTTPS 호출에 맞는다.

**단점**

- 공개 DNS는 최대 10분이 걸릴 수 있다. 이번 세션에서 컨테이너는 `https://<pod이름>.<tailnet>.ts.net/`을 출력했지만, 마지막 공개 DNS 조회에는 A 레코드가 없었다. 성공한 모델 호출은 Runpod 프록시로 갔다.
- Ephemeral 노드는 마지막 활동 후 30분에서 48시간 사이에 지워진다. 그 전에 같은 이름으로 다시 만들면 `qwen38-27b-1`이 되고, 그 이름은 자동으로 되돌아가지 않는다.
- Funnel은 베타이고 대역폭 제한 수치는 공개되어 있지 않다. 공개 포트는 443, 8443, 10000뿐이다.
- 인증 키는 최대 90일이다. 태그가 있으면 기본 허용 대상인 `autogroup:member`에서 빠진다.
- Runpod의 JSON entrypoint 덮어쓰기는 이 이미지에서 실행된 것을 보았고, 공식 보장으로 확인하지는 못했다.

### 2. Pod를 종료하지 않고 멈춘다

같은 Pod ID가 살아 있는 동안 프록시 호스트는 유지된다. GPU 요금은 멈추고, 컨테이너 디스크 요금도 중지 중에는 없다. 이 스펙은 네트워크 볼륨이 없어 중지 시 컨테이너 디스크의 모델 캐시가 지워진다.

**장점**

- 추가 터널이나 맥 설치가 없다.
- 같은 물리 머신에서 다시 켜지면 프록시 호스트가 그대로다.

**단점**

- Pod 레코드를 남겨 두므로, 종료하고 자리를 놓는 조건과 맞지 않는다.
- Runpod 문서상 Pod ID는 물리 머신에 묶인다. GPU를 잃거나 마이그레이션되면 새 ID와 새 프록시 주소가 나온다. 중지로는 주소를 끝까지 고정할 수 없다.

### 3. 재생성될 때마다 Runpod 프록시를 호출 코드에 넣는다

`podrouter`이 출력하는 주소가 이 방식이다. Pod가 바뀌면 호출부의 `base_url`도 바뀐다.

**장점**

- 터널, 키, DNS 전파가 없다. 모델 호출은 이 주소로 이미 성공했다.

**단점**

- 주소를 코드에 고정한다는 조건을 충족하지 못한다.

### 4. Pod 안에서 Cloudflare Named Tunnel

`cloudflared`가 계정에 붙은 고정 호스트명을 컨테이너 포트에 연결한다. 맥 설치는 필요 없고, Pod를 종료해도 호스트명은 남는다.

**장점**

- 세 가지 필수 조건을 도메인만 있으면 만족한다.
- 호스트명 충돌로 `-1`이 붙는 Tailscale 머신 이름 규칙은 타지 않는다.

**단점**

- 그 Cloudflare 계정에 zone이 있어야 한다. 이전에 확인한 토큰 계정에는 zone이 없었고, `example.com`은 수정할 수 있는 도메인이 아니었다.

일찍 제외한 안은 두 가지다. 공인 IP와 DDNS는 재생성과 리셋마다 IP와 외부 포트가 바뀌어 HTTPS 443 고정 주소가 되지 않는다. Runpod Global Networking의 `<pod-id>.runpod.internal`은 같은 계정의 다른 Pod만 접근할 수 있고, 재생성되면 이름도 바뀐다.

## Decision

**Tailscale Funnel을 Pod 안에서 켜고, 호출 주소는 `https://<pod이름>.<tailnet>.ts.net/v1`로 고정한다.**

선택 이유: 종료 뒤에도 같은 호스트명을 Pod가 스스로 다시 등록할 수 있는 방법이다. 맥용 Tailscale이 필요 없다.

Pod를 멈추기만 하는 안은 주소가 마이그레이션과 GPU 회수 뒤에도 남으려면 Pod ID가 계속 같아야 한다. Runpod는 그 경우 새 ID를 발급한다. 프록시를 코드에 넣는 안은 재생성마다 호스트가 바뀐다. Cloudflare Named Tunnel은 조건에는 맞지만, 확인된 계정에는 zone이 없었다.

이 선택에는 공개 DNS 지연, 이름 뒤에 붙는 `-1`, 베타와 비공개 대역폭 제한, 최대 90일 키, 태그 금지, entrypoint 덮어쓰기의 공식 미보장을 감수한다.

## Consequences

### Positive

- 호출 코드가 Pod ID를 몰라도 된다.
- 등록과 터널이 Pod 수명 안에 끝나므로 개발 맥을 tailnet에 넣지 않아도 된다.
- 관측된 결과: 수정 후 Pod는 `tailscale up`을 통과했고, 컨테이너 로그에 Funnel URL과 백그라운드 시작이 남았다. 공개 주소로 모델을 호출해 성공한 기록은 아직 없다.

### Negative

- 첫 공개 조회는 최대 10분 비어 있을 수 있다. 그 사이 확인은 그 Pod의 프록시를 써야 하고, 그 프록시는 다음 생성에서 버린다.
- 이전 ephemeral 노드가 남아 있으면 고정해 둔 호스트명과 실제 이름이 어긋난다. 호출부를 고치거나, 이전 노드가 사라진 뒤 다시 만들어야 한다.
- 인증 키를 90일 안에 교체해야 한다. 교체 후 웹 서버를 다시 켜야 `.env`의 새 키가 생성 프로세스에 들어간다.
- Funnel 첫 허용은 테일넷 소유자가 승인 화면을 한 번 열어야 한다. 이 테일넷에서는 이미 했다.
- `runpodctl`이 llama.cpp 이미지에 없으면 3시간 타이머가 동작하지 않는다. 종료는 `podrouter down`에 맡긴다.
- CLI는 전체 `ts.net` URL을 출력하지 않고 `funnel_hostname=<파드 이름>`만 출력한다. 테일넷 접미사는 이 문서와 Pod 로그에서 확인한 값이다.

## Reconsideration Conditions

아래가 관측되면 이 결정과 대안을 다시 비교한다. 조건이 맞았다고 구현이 자동으로 바뀌지는 않는다.

- 이 계정에 zone이 있는 도메인을 쓰게 되는 경우. 비교 대상은 Pod 안의 Cloudflare Named Tunnel이다.
- `<pod이름>.<tailnet>.ts.net`의 공개 DNS가 Funnel 시작 후 10분을 넘겨 반복해서 비는 경우.
- Funnel 대역폭 제한 때문에 실제 호출이 실패하는 경우. 제한 수치는 아직 공개되어 있지 않다.
- 같은 이름으로 바로 다시 만들었을 때 `-1` 호스트명이 호출 고정보다 자주 문제가 되는 경우.
