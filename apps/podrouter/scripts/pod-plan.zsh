#!/bin/zsh
# Claude가 Pod YAML을 만들 때 쓰는 실행기.
# render_plan.py가 저장소 .env에서 RUNPOD_API_KEY를 읽는다. 키는 출력하지 않는다.
set -e

repo="${0:A:h:h:h:h}"
exec "$repo/.venv/bin/python" "$repo/.grok/skills/podrouter-yaml/scripts/render_plan.py" "$@"
