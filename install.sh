#!/bin/zsh
# podrouter 설치 스크립트 (macOS)
#
#   ./install.sh                 Python 환경 + 맥 앱을 ~/Applications 에 설치
#   ./install.sh --dest DIR      앱을 DIR 에 설치 (예: /Applications)
#   ./install.sh --python-only   Python 환경(.venv)만 준비
#
# 이 폴더 위치를 앱 안에 기록하므로, 앱이 프로젝트 폴더를 자동으로 찾는다.
# 키(.env)는 만들거나 읽지 않는다. 키는 앱의 Keys 화면에서 넣는다.
set -euo pipefail

REPO="${0:A:h}"
APP_NAME="podrouter"
BUNDLE_ID="io.github.hrunj1230.podrouter"
DEST="$HOME/Applications"
PYTHON_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) DEST="${2:?--dest 뒤에 폴더를 적으세요}"; shift 2 ;;
        --python-only) PYTHON_ONLY=1; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
    esac
done

step() { print -P "%F{cyan}==>%f $1"; }
fail() { print -P "%F{red}오류:%f $1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "macOS 에서만 설치할 수 있습니다."
[ -f "$REPO/pyproject.toml" ] && [ -f "$REPO/src/podrouter/cli.py" ] || fail "프로젝트 폴더에서 실행하세요: $REPO"

# 1. Python 환경
step "Python 환경 준비 (.venv)"
if command -v uv >/dev/null 2>&1; then
    [ -x "$REPO/.venv/bin/python" ] || uv venv --python ">=3.10" "$REPO/.venv"
    uv pip install --python "$REPO/.venv/bin/python" -e "$REPO" --quiet
else
    PY="$(command -v python3 || true)"
    [ -n "$PY" ] || fail "python3 가 없습니다. https://www.python.org 또는 'brew install python' 으로 설치하세요."
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
        || fail "Python 3.10 이상이 필요합니다. 지금: $("$PY" --version 2>&1)"
    [ -x "$REPO/.venv/bin/python" ] || "$PY" -m venv "$REPO/.venv"
    "$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
    "$REPO/.venv/bin/python" -m pip install --quiet -e "$REPO"
fi
"$REPO/.venv/bin/python" -m podrouter --help >/dev/null || fail "podrouter 설치 확인에 실패했습니다."

if [ "$PYTHON_ONLY" = 1 ]; then
    step "완료: Python 환경만 준비했습니다."
    exit 0
fi

# 2. 맥 앱 빌드
command -v swift >/dev/null 2>&1 || fail "swift 가 없습니다. 'xcode-select --install' 로 Xcode 명령줄 도구를 설치하세요."
PKG="$REPO/apps/podrouter"
step "맥 앱 빌드 (swift build -c release)"
swift build -c release --package-path "$PKG"
BIN="$(swift build -c release --package-path "$PKG" --show-bin-path)/$APP_NAME"
[ -x "$BIN" ] || fail "빌드 결과를 찾지 못했습니다: $BIN"

# 3. .app 묶음 만들기
STAGE="$PKG/.build/$APP_NAME.app"
step ".app 묶음 만들기"
rm -rf "$STAGE"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"
cp "$BIN" "$STAGE/Contents/MacOS/$APP_NAME"
print -r -- "$REPO" > "$STAGE/Contents/Resources/repo-path.txt"
cp "$PKG/Resources/AppIcon.icns" "$STAGE/Contents/Resources/AppIcon.icns"
VERSION="$("$REPO/.venv/bin/python" -c 'import tomllib,sys; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$REPO/pyproject.toml" 2>/dev/null || echo 0.1.0)"
cat > "$STAGE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key><string>$APP_NAME</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
# 이 맥에서만 쓰는 임시 서명. 직접 빌드한 앱이라 Gatekeeper 경고가 뜨지 않는다.
codesign --force --sign - "$STAGE" >/dev/null 2>&1 || true

# 4. 설치
step "설치: $DEST/$APP_NAME.app"
mkdir -p "$DEST"
pkill -x "$APP_NAME" 2>/dev/null || true
rm -rf "$DEST/$APP_NAME.app"
cp -R "$STAGE" "$DEST/$APP_NAME.app"
# Finder/Dock 이 새 아이콘을 바로 읽도록 등록 정보를 갱신한다.
touch "$DEST/$APP_NAME.app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST/$APP_NAME.app" >/dev/null 2>&1 || true

print ""
step "완료"
print "  앱:        $DEST/$APP_NAME.app"
print "  프로젝트:  $REPO"
print "  실행:      open \"$DEST/$APP_NAME.app\""
print ""
print "  처음 실행하면 Keys 화면에서 RUNPOD_API_KEY 를 저장하세요."
print "  프로젝트 폴더를 옮기면 ./install.sh 를 다시 실행하세요."
