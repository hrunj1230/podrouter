#!/bin/zsh
# 새 버전을 GitHub Release 와 Homebrew tap 에 배포한다.
#
#   ./scripts/release.sh 0.2.0
#   ./scripts/release.sh --check-only     테스트와 빌드만 (커밋·push·업로드 없음)
#
# 바꿀 수 있는 값 (환경변수):
#   RA_REPO  앱 저장소   기본 hrunj1230/podrouter
#   RA_TAP   tap 저장소  기본 hrunj1230/homebrew-tap
set -euo pipefail

REPO_DIR="${0:A:h:h}"
RA_REPO="${RA_REPO:-hrunj1230/podrouter}"
RA_TAP="${RA_TAP:-hrunj1230/homebrew-tap}"
cd "$REPO_DIR"

step() { print -P "%F{cyan}==>%f $1"; }
fail() { print -P "%F{red}오류:%f $1" >&2; exit 1; }

ARG="${1:-}"
[ -n "$ARG" ] || fail "버전을 적으세요. 예: ./scripts/release.sh 0.2.0"
CHECK_ONLY=0
if [ "$ARG" = "--check-only" ]; then
    CHECK_ONLY=1
else
    [[ "$ARG" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' ]] || fail "버전 형식은 0.2.0 처럼 적습니다: $ARG"
    VERSION="$ARG"
    command -v gh >/dev/null || fail "gh(GitHub CLI)가 필요합니다: brew install gh"
    [ -z "$(git status --porcelain)" ] || fail "커밋하지 않은 변경이 있습니다. 먼저 커밋하세요."
    git rev-parse "v$VERSION" >/dev/null 2>&1 && fail "태그 v$VERSION 이 이미 있습니다."
fi

step "테스트"
.venv/bin/python -m pytest -q

if [ "$CHECK_ONLY" = 0 ]; then
    step "버전 $VERSION 으로 올리기"
    sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
fi

./scripts/build-app.sh
ZIP="build/podrouter.zip"
SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"

if [ "$CHECK_ONLY" = 1 ]; then
    step "검사만 했습니다. zip: $ZIP"
    exit 0
fi

step "커밋 · 태그 · push"
git add pyproject.toml
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"
git push origin HEAD "v$VERSION"

step "GitHub Release 만들기"
gh release create "v$VERSION" "$ZIP" --repo "$RA_REPO" --title "v$VERSION" --notes "$(cat <<NOTES
## 설치

\`\`\`bash
brew install --cask ${RA_TAP%%/*}/tap/podrouter
\`\`\`

업데이트: \`brew upgrade --cask podrouter\`

직접 받기: podrouter.zip 을 풀어 Applications 로 옮긴 뒤 한 번만 실행하세요.
\`xattr -dr com.apple.quarantine /Applications/podrouter.app\`
NOTES
)"

step "Homebrew tap 갱신 ($RA_TAP)"
TAP_DIR="$(mktemp -d)"
gh repo clone "$RA_TAP" "$TAP_DIR" -- --quiet
mkdir -p "$TAP_DIR/Casks"
sed -e "s/@VERSION@/$VERSION/" -e "s/@SHA256@/$SHA/" -e "s#@REPO@#$RA_REPO#g" \
    packaging/homebrew/podrouter.rb.template > "$TAP_DIR/Casks/podrouter.rb"
git -C "$TAP_DIR" add Casks/podrouter.rb
git -C "$TAP_DIR" commit -m "podrouter $VERSION"
git -C "$TAP_DIR" push
rm -rf "$TAP_DIR"

step "완료: v$VERSION"
print "  설치: brew install --cask ${RA_TAP%%/*}/tap/podrouter"
