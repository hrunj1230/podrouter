#!/bin/zsh
# 배포용 podrouter.app 과 build/podrouter.zip 을 만든다.
#
#   ./scripts/build-app.sh            universal(arm64 + x86_64) 빌드, 실패하면 이 맥 아키텍처만
#   ./scripts/build-app.sh --native   이 맥 아키텍처만
#
# 결과 앱은 git 저장소 없이 동작한다. podrouter 은 wheel 로 앱 안에 들어가고,
# 처음 실행할 때 앱이 ~/Library/Application Support/podrouter/venv 에 설치한다.
# 키와 Pod 설정은 ~/podrouter 에 저장된다.
set -euo pipefail

REPO="${0:A:h:h}"
APP_NAME="podrouter"
BUNDLE_ID="io.github.hrunj1230.podrouter"
PKG="$REPO/apps/podrouter"
OUT="$REPO/build"
NATIVE=0
[ "${1:-}" = "--native" ] && NATIVE=1

step() { print -P "%F{cyan}==>%f $1"; }
fail() { print -P "%F{red}오류:%f $1" >&2; exit 1; }

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -n 1)"
[ -n "$VERSION" ] || fail "pyproject.toml 에서 version 을 읽지 못했습니다."

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || fail "python3 가 필요합니다 (wheel 빌드용)."

# 1. Swift 앱 빌드
BIN=""
if [ "$NATIVE" = 0 ]; then
    step "맥 앱 빌드 (universal)"
    if swift build -c release --package-path "$PKG" --arch arm64 --arch x86_64 >/dev/null 2>&1; then
        BIN="$(swift build -c release --package-path "$PKG" --arch arm64 --arch x86_64 --show-bin-path)/$APP_NAME"
    else
        print "  universal 빌드 실패 (전체 Xcode 필요). 이 맥 아키텍처로만 빌드합니다."
    fi
fi
if [ -z "$BIN" ]; then
    step "맥 앱 빌드 ($(uname -m))"
    swift build -c release --package-path "$PKG"
    BIN="$(swift build -c release --package-path "$PKG" --show-bin-path)/$APP_NAME"
fi
[ -x "$BIN" ] || fail "빌드 결과를 찾지 못했습니다: $BIN"

# 2. .app 묶음
APP="$OUT/$APP_NAME.app"
RES="$APP/Contents/Resources/podrouter"
step ".app 묶음 만들기 (v$VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/claude/skills"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"
cp "$REPO/apps/podrouter/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

step "podrouter wheel 만들기"
WHEEL_TMP="$(mktemp -d)"
"$PY" -m pip wheel --quiet --no-deps --disable-pip-version-check -w "$WHEEL_TMP" "$REPO"
cp "$WHEEL_TMP"/podrouter-*.whl "$RES/"
rm -rf "$WHEEL_TMP" "$REPO/build/lib" "$REPO/build/bdist."* 2>/dev/null || true

cp "$REPO/.grok/skills/podrouter-yaml/scripts/render_plan.py" "$RES/render_plan.py"
cp -R "$REPO/.claude/skills/podrouter-"* "$RES/claude/skills/"
cp "$REPO/.claude/settings.json" "$RES/claude/settings.json"

cat > "$APP/Contents/Info.plist" <<PLIST
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

# Apple 공증 없는 임시(ad-hoc) 서명. Homebrew cask 가 격리 속성을 지워 경고 없이 열린다.
codesign --force --deep --sign - "$APP" >/dev/null

# 3. zip
ZIP="$OUT/$APP_NAME.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"

print ""
step "완료"
print "  앱:      $APP"
print "  zip:     $ZIP"
print "  아키텍처: $(lipo -archs "$APP/Contents/MacOS/$APP_NAME")"
print "  sha256:  $SHA"
