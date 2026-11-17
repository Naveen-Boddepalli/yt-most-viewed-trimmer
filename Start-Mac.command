#!/bin/bash
# YouTube Highlight Trimmer: double-click this file to start the app (macOS).
#
# Nothing needs to be installed first. On the first run this downloads `uv`
# (a small installer tool, no admin password needed), a private copy of
# Python, and the app's libraries into this folder. That takes a few minutes.
# Later starts take a few seconds. The app then opens in your web browser.
#
# Also used by start-linux.sh. Extra arguments are passed on to Streamlit.

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
    echo
    read -r -p "Press Enter to close this window." _
    exit 1
}

echo "=== YouTube Highlight Trimmer ==="
echo

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "First-time setup: installing uv (no admin password needed)..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh; then
        echo "Could not install uv. Check your internet connection and try again."
        pause_and_exit
    fi
fi

first_run=0
if [ ! -x ".venv/bin/python" ]; then
    first_run=1
    echo "First-time setup: downloading Python and the app's libraries."
    echo "This takes a few minutes and only happens once."
    if ! uv venv --quiet --python 3.12 .venv; then
        echo "Could not set up Python. Check your internet connection and try again."
        pause_and_exit
    fi
fi

# Install missing libraries and update the YouTube downloader every start:
# YouTube changes often and old versions of yt-dlp stop working.
echo "Checking for updates..."
if ! uv pip install --quiet --python .venv/bin/python -r requirements.txt \
        --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs --upgrade-package pytubefix; then
    if [ "$first_run" = 1 ]; then
        rm -rf .venv
        echo "Setup failed. Check your internet connection and try again."
        pause_and_exit
    fi
    echo "Could not check for updates (offline?). Starting with the installed version."
fi

export YT_TRIMMER_SINGLE_USER=1
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

echo
echo "Starting... the app will open in your web browser."
echo "Keep this window open while you use the app. Close it to stop the app."
echo

.venv/bin/python -m streamlit run app.py \
    --server.headless false \
    --server.address 127.0.0.1 --server.fileWatcherType none \
    "$@" || pause_and_exit
