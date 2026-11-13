#!/usr/bin/env bash
# Build the GNSS Bridge APK inside Docker (no Android SDK/NDK needed on host).
#
# Usage (from repo root):
#   bash builder/build_apk_docker.sh
# Options:
#   --rebuild   Force rebuild of the Docker image
#   --arch=ABI  Android ABI (default: armeabi-v7a). Example: --arch=arm64-v8a
#
# Output:
#   dist/*.apk
set -euo pipefail

IMAGE_NAME="gnss-bridge-builder"
CACHE_VOLUME="gpsbridge-p4a-cache"
ARCH="armeabi-v7a"
REBUILD=0

for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --arch=*) ARCH="${arg#--arch=}" ;;
    -h|--help)
      sed -n '1,20p' "$0"; exit 0 ;;
    *)
      echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# Resolve project root (directory containing this script's parent).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required but not installed/found in PATH." >&2
  exit 1
fi

if [[ "$REBUILD" -eq 1 ]] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "==> Building Docker image '$IMAGE_NAME'..."
  docker build -t "$IMAGE_NAME" -f builder/Dockerfile .
fi

mkdir -p dist

# p4a stores downloads/build state under its storage dir; mounting a named
# volume makes subsequent builds much faster.
P4A_STORAGE="/p4a-cache"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

# Build the p4a --requirements list from builder/requirements.txt (the single
# source of truth for the app's Python dependencies). One dependency per line;
# # comments and blank lines are ignored. The pinned python3/hostpython3
# versions are prepended here (they are build config, not app dependencies).
REQUIREMENTS_FILE="builder/requirements.txt"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  echo "ERROR: $REQUIREMENTS_FILE not found." >&2
  exit 1
fi
REQS="$(grep -vE '^[[:space:]]*(#|$)' "$REQUIREMENTS_FILE" | paste -sd, -)"
if [[ -z "$REQS" ]]; then
  echo "ERROR: no requirements found in $REQUIREMENTS_FILE." >&2
  exit 1
fi
REQUIREMENTS="python3==3.13.18,hostpython3==3.13.18,${REQS}"
echo "==> Requirements: ${REQUIREMENTS}"

echo "==> Building APK (arch=$ARCH)..."
docker run --rm \
  -v "${PROJECT_DIR}:/app" \
  -v "${CACHE_VOLUME}:${P4A_STORAGE}" \
  -e ANDROID_HOME=/opt/android-sdk \
  -e ANDROID_SDK_ROOT=/opt/android-sdk \
  -e ANDROID_NDK_HOME=/opt/android-sdk/ndk/25.2.9519653 \
  -e ANDROIDNDK=/opt/android-sdk/ndk/25.2.9519653 \
  -w /app \
  --entrypoint bash \
  "$IMAGE_NAME" -lc "
    set -euo pipefail
    p4a apk \
      --bootstrap=sdl2 \
      --requirements=${REQUIREMENTS} \
      --name 'GNSS Bridge' \
      --package org.gnssbridge.app \
      --version 1.0 \
      --private /app \
      --dist_name gnssbridge \
      --add-source /app/java \
      --service=gps:services/gps.py:foreground:sticky:foregroundServiceType=location \
      --permission ACCESS_MOCK_LOCATION BLUETOOTH BLUETOOTH_ADMIN ACCESS_FINE_LOCATION \
      --android_api 34 \
      --ndk-api 23 \
      --minsdk 23 \
      --allow-minsdk-ndkapi-mismatch \
      --arch=${ARCH} \
      --storage-dir ${P4A_STORAGE} \
      --sdk-dir /opt/android-sdk \
      --ndk-dir /opt/android-sdk/ndk/25.2.9519653

    echo '==> Locating APK...'
    APK=\$(find ${P4A_STORAGE}/dists /root/.local/share/python-for-android/dists /app -name '*.apk' -path '*gnssbridge*' 2>/dev/null | head -n 1 || true)
    if [ -z \"\${APK}\" ]; then
      APK=\$(find ${P4A_STORAGE} /root/.local/share/python-for-android /app -name '*.apk' 2>/dev/null | head -n 1 || true)
    fi
    if [ -z \"\${APK}\" ]; then
      echo 'ERROR: APK not found after build.' >&2
      exit 1
    fi
    mkdir -p /app/dist
    cp -f \"\${APK}\" /app/dist/
    chown ${HOST_UID}:${HOST_GID} /app/dist/*.apk 2>/dev/null || true
    echo \"==> APK: /app/dist/\$(basename \"\${APK}\")\"
  "

# Clean up Python bytecode caches (__pycache__/ and stray .pyc) created during
# the build, so they don't accumulate in the project tree.
echo "==> Cleaning __pycache__..."
find "${PROJECT_DIR}" -type d -name '__pycache__' -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
find "${PROJECT_DIR}" -type f -name '*.pyc' -not -path '*/.git/*' -delete 2>/dev/null || true

echo "==> Done. APK(s) in ${PROJECT_DIR}/dist:"
ls -lh "${PROJECT_DIR}/dist"/*.apk 2>/dev/null || echo "(no .apk found)"
