# Builder image for the GPS Bridge APK (python-for-android + Kivy + pynmea2).
# Produces a debug APK for Android (default: armeabi-v7a, compileSdk 34, NDK API 23).
#
# Build:
#   docker build -t gps-bridge-builder -f builder/Dockerfile .
# Run (from repo root):
#   docker run --rm -v "$PWD":/app -v gpsbridge-p4a-cache:/p4a-cache gps-bridge-builder

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    # Android
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    ANDROID_NDK_HOME=/opt/android-sdk/ndk/25.2.9519653 \
    ANDROIDNDK=/opt/android-sdk/ndk/25.2.9519653 \
    # p4a cache (mount a volume here for faster rebuilds)
    P4A_CACHE_DIR=/p4a-cache \
    # Java
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/34.0.0:${JAVA_HOME}/bin:${PATH}"

# Host build dependencies required by python-for-android recipes.
RUN apt-get update && apt-get install -y --no-install-recommends \
      autoconf automake libtool libtool-bin pkg-config build-essential \
      ca-certificates curl git unzip zip \
      openjdk-17-jdk-headless \
      python3 python3-pip python3-venv python3-dev \
      zlib1g-dev libncurses-dev libffi-dev libssl-dev libltdl-dev \
      cmake lld ccache \
      file patch gettext autoconf-archive texinfo \
    && rm -rf /var/lib/apt/lists/*

# Help autoreconf/libtool find system m4 macros (fixes some libffi builds).
ENV ACLOCAL_PATH=/usr/share/aclocal

# python-for-android + Cython (used by Kivy/pyjnius/android recipes).
# Pin a known-compatible Cython range; p4a itself is installed latest.
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir "Cython>=0.29,<3.1" python-for-android

# Patch the built-in p4a "android" recipe: its setup.py imports Cython at top
# level, but python -m build uses an isolated env that only installs setuptools
# unless declared in [build-system]. Adding pyproject.toml makes the build
# reliable across p4a versions.
RUN P4A_RECIPES="$(python3 -c 'import pythonforandroid, os; print(os.path.join(os.path.dirname(pythonforandroid.__file__), "recipes", "android", "src"))')" \
    && printf '%s\n' \
       '[build-system]' \
       'requires = ["setuptools>=40.8.0", "Cython>=0.29,<3.1", "wheel"]' \
       'build-backend = "setuptools.build_meta"' \
       > "${P4A_RECIPES}/pyproject.toml"

# Android SDK command-line tools + required packages + NDK.
ARG CMDLINE_TOOLS_URL=https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
RUN mkdir -p "${ANDROID_HOME}/cmdline-tools" /tmp/cmdline \
    && curl -fsSL "${CMDLINE_TOOLS_URL}" -o /tmp/cmdline-tools.zip \
    && unzip -q /tmp/cmdline-tools.zip -d /tmp/cmdline \
    && mv /tmp/cmdline/cmdline-tools "${ANDROID_HOME}/cmdline-tools/latest" \
    && rm -rf /tmp/cmdline-tools.zip /tmp/cmdline \
    && yes | sdkmanager --licenses >/dev/null || true \
    && sdkmanager --install \
         "platform-tools" \
         "platforms;android-34" \
         "build-tools;34.0.0" \
         "ndk;25.2.9519653"

WORKDIR /app

# Keep p4a state in /p4a-cache (mount a volume for persistence).
ENV ANDROID_SDK_HOME=/p4a-cache/android-sdk-home

ENTRYPOINT ["p4a"]
CMD ["--help"]
