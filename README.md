# gpu-up

설정 파일 하나로 RunPod **Pod** 를 만들고, `/health`가 200이 되면 프록시 URL을 출력한다. 부팅·헬스 타임아웃, 예외, Ctrl+C에서는 그 파드를 종료한다. 컨테이너 시작 명령에는 같은 TTL로 `runpodctl`이 현재 파드를 지우도록 타이머를 넣는다.

```bash
pip install .
export RUNPOD_API_KEY=...
export HF_TOKEN=...
export VLLM_API_KEY=...
gpu-up up examples/serve.yaml --api-key '${RUNPOD_API_KEY}'
gpu-up status qwen-7b-serve --api-key '${RUNPOD_API_KEY}'
gpu-up ls --api-key '${RUNPOD_API_KEY}'
gpu-up down qwen-7b-serve --api-key '${RUNPOD_API_KEY}'
gpu-up down --all --api-key '${RUNPOD_API_KEY}'
```

`--wait=false`는 파드를 만든 뒤 헬스체크 없이 파드 ID와 프록시 URL만 출력하고 종료한다.

비밀값은 `${ENV}`로만 적는다. CLI는 설정 파일을 다시 쓰지 않고, 해석된 값을 stdout/stderr에 남기지 않는다. `lifecycle.ttl`이 없거나 `3h` / `30m` 형식이 아니면 파드를 만들지 않는다.

GPU 후보는 위에서부터 시도한다. `gpu.cloud`의 시간당 가격이 `gpu.max_price_per_hour`보다 크면 그 후보는 요청하지 않는다. 재고가 없으면 다음 후보로 넘어간다.

같은 이름이라도 설정 해시가 다르면 기존 URL을 재사용하지 않는다. `ls`와 `down --all`은 이 도구가 만든 파드만 다룬다.

RunPod 호출은 `runpod==1.12.0`의 Pod API(`create_pod`, `get_pod`, `get_pods`, `terminate_pod`)만 사용한다. SDK가 읽는 `RUNPOD_API_BASE_URL`로 GraphQL 호스트를 바꿀 수 있다. 헬스체크를 다른 호스트로 보내려면 `GPU_UP_HEALTH_BASE`를 설정한다. 출력 URL은 항상 `https://<pod-id>-<port>.proxy.runpod.net`이다.
