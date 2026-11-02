#!/usr/bin/env bash
# 一键构建 macOS 安装包（Apple Silicon）：后端（PyInstaller）+ uv + Electron → desktop/release/*.dmg
#
#   packaging/build-mac.sh              完整构建（含测试）
#   packaging/build-mac.sh --skip-tests 跳过测试
#
# 前置：uv（https://docs.astral.sh/uv/）、Node.js 20+。首次构建需要联网下载依赖与 uv 二进制。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SKIP_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1：$2" >&2; exit 1; }; }

need uv "安装方法见 https://docs.astral.sh/uv/getting-started/installation/"
need node "请安装 Node.js 20 或更新版本（https://nodejs.org/）"
need npm "请安装 Node.js 20 或更新版本（https://nodejs.org/）"
# Only Apple Silicon is supported.  Everything below is pinned to arm64 (fetch_uv --target mac-arm64,
# electron-builder --arm64) while PyInstaller always builds for the host, so on an Intel Mac the three
# halves of the bundle would disagree and the app would fail at launch with nothing in the build log.
if [ "$(uname -m)" != "arm64" ]; then
  echo "只支持 Apple Silicon（M 系列芯片）。当前是 $(uname -m)。" >&2
  echo "在 Intel Mac 上构建会得到一个架构不匹配、无法启动的包，所以这里直接停下。" >&2
  exit 1
fi

step "Python 依赖（uv sync）"
uv sync --group build

if [ "$SKIP_TESTS" = 0 ]; then
  step "后端测试"
  uv run pytest -q
fi

if [ ! -f desktop/build/icon.icns ]; then
  step "生成图标"
  uv run python packaging/make_icons.py
fi

step "打包后端（PyInstaller）"
rm -rf build/pyinstaller
uv run pyinstaller packaging/backend.spec --noconfirm --log-level WARN \
  --distpath build/pyinstaller/dist --workpath build/pyinstaller/work

step "随包附带 uv"
uv run python packaging/fetch_uv.py --target mac-arm64

step "冒烟测试：后端能否启动"
BACKEND="build/pyinstaller/dist/backend/agent4sci-server"
READY="$(A4S_DATA_DIR="$(mktemp -d)" A4S_SECRETS=memory "$BACKEND" --port 0 --demo --exit-on-stdin-close </dev/null | head -n 1 || true)"
case "$READY" in
  *'"ready"'*) echo "后端就绪：$READY" ;;
  *) echo "后端启动失败：$READY" >&2; exit 1 ;;
esac

step "前端依赖（npm ci）"
cd desktop
npm ci --no-audit --no-fund
if [ "$SKIP_TESTS" = 0 ]; then
  npm run typecheck
  npm test
fi

step "构建 Electron 应用"
npm run build
rm -rf release
# Public distribution needs a real Developer ID: set CSC_NAME (and CSC_LINK/CSC_KEY_PASSWORD when the
# certificate is not in the login keychain), plus APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID to
# notarize. Without CSC_NAME the app is ad-hoc signed below: fine on this Mac, warns on anyone else's.
SIGN=()
if [ -n "${CSC_NAME:-}" ]; then
  SIGN+=("-c.mac.identity=$CSC_NAME" -c.mac.hardenedRuntime=true)
  [ -n "${APPLE_ID:-}" ] && SIGN+=(-c.mac.notarize=true)
else
  export CSC_IDENTITY_AUTO_DISCOVERY=false
fi
npx electron-builder --mac dir --arm64 --publish never ${SIGN[@]+"${SIGN[@]}"}
APP="$(find release -maxdepth 2 -name '*.app' -type d | head -n 1)"
[ -n "$APP" ] || { echo "没有找到 .app" >&2; exit 1; }

if [ -n "${CSC_NAME:-}" ]; then
  step "证书签名（electron-builder 已用 $CSC_NAME 签名）"
  codesign --verify --deep --strict "$APP" && echo "签名校验通过：$APP"
else
  step "ad-hoc 签名（Apple Silicon 必须有有效签名才能运行）"
  codesign --force --deep --sign - "$APP"
  codesign --verify --deep --strict "$APP" && echo "签名校验通过：$APP"
fi

step "生成 dmg / zip"
npx electron-builder --mac dmg zip --arm64 --prepackaged "$APP" --publish never ${SIGN[@]+"${SIGN[@]}"}

step "完成"
ls -lh release/*.dmg release/*.zip 2>/dev/null || true
echo
echo "首次打开未签名的应用：在“应用程序”中右键 Agent4Sci →“打开”，或运行"
echo "  xattr -dr com.apple.quarantine /Applications/Agent4Sci.app"
