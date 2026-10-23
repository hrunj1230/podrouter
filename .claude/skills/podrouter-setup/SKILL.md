---
name: podrouter-setup
description: >
  podrouter Mac 앱 설정에 필요한 Runpod API 키 및 Tailscale 인증 키(Auth Key) 발급 클릭 경로를 안내합니다.
  사용자가 /podrouter-setup 명령을 실행하거나 Runpod 또는 Tailscale 키 발급 방법을 물어볼 때 사용합니다.
---

# podrouter 키 설정 안내

이 스킬은 podrouter Mac 앱 연동에 필요한 Runpod API 키 및 Tailscale 인증 키(Auth Key)의 발급 클릭 경로(Click Path)만 안내합니다.

## 필수 보안 및 운영 지침

1. **키 입력 위치 및 채팅 입력 금지**:
   - 발급받은 키는 반드시 podrouter Mac 앱의 보안 입력 필드(SecureField)에 직접 붙여넣어야 합니다.
   - Claude와의 대화창(트랜스크립트)에는 절대로 키를 붙여넣지 마십시오.
2. **키 유출 시 즉시 폐기**:
   - 만약 메시지나 대화에 이미 키 값이 포함되어 노출된 경우, 즉시 해당 콘솔에서 키를 폐기(Revoke)하도록 사용자에게 안내하십시오.
   - 노출된 키 값을 절대로 대화창에 반복하거나 출력하지 마십시오.
3. **환경 파일 및 시크릿 보호**:
   - `.env` 및 `.env.*` 파일을 읽지 마십시오.
   - 어떤 시크릿이나 비밀 키 값도 출력하지 마십시오.
4. **명령어 및 리소스 생성 제한**:
   - `podrouter up` 또는 `podrouter down` 명령을 실행하지 마십시오.
   - Pod을 직접 생성하지 마십시오.
   - `example.com` 도메인 설정이나 Cloudflare 설정을 변경하지 마십시오.
5. **타이머 운영 안내**:
   - 타이머로 GitHub Actions를 제안하지 마십시오.
   - 타이머는 이 Mac에 로그인되어 있고 깨어 있는(awake) 동안에만 실행되며, Mac이 꺼져 있거나 잠자기(sleep) 상태일 때는 아무런 작업도 수행하지 않습니다.

---

## 1. Runpod API 키 발급 클릭 경로

1. 웹 브라우저에서 https://console.runpod.io/user/settings 접속
2. 메뉴에서 **Settings** 선택
3. **API Keys** 선택
4. 키 생성(**Create Key**) 버튼 클릭
5. 생성된 키 값을 **한 번만 복사** (이후 다시 확인할 수 없음)
6. 복사한 키를 podrouter Mac 앱의 API 키 입력란(SecureField)에 붙여넣기 (대화창에 붙여넣지 마십시오)

---

## 2. Tailscale 인증 키(Auth Key) 발급 클릭 경로

> **참고**: Tailscale 인증 키는 사용자가 고정적이고 안정적인 Funnel 도메인 이름을 원하는 경우에만 발급합니다.

1. 웹 브라우저에서 https://login.tailscale.com/admin/settings/keys 접속
2. **Settings** 설정값 지정:
   - **Reusable**: 활성화(체크)
   - **Ephemeral**: 활성화(체크) — *중요: Ephemeral 설정은 필수 핵심 속성입니다.*
   - **Tags**: 태그 없음(no tags)
   - **Expiry**: 최대 90일 이하(at most 90 days)
3. 키 생성 완료 후 값 복사 (값은 `tskey-auth-`로 시작함)
4. 복사한 키를 podrouter Mac 앱의 Tailscale 키 입력란(SecureField)에 붙여넣기 (대화창에 붙여넣지 마십시오)

> **중요 안내**:
> Mac에서 `tailscale up` 명령을 실행하도록 안내하지 마십시오. 이 Mac에는 Tailscale 클라이언트가 설치되어 있지 않으며 필요하지도 않습니다. 실행되는 Pod 내부에서 자체적으로 Tailscale에 등록됩니다.
