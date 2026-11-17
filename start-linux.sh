#!/bin/bash
# YouTube Highlight Trimmer: run `bash start-linux.sh` to start the app (Linux).
# Same steps as the macOS launcher.
exec bash "$(dirname "$0")/Start-Mac.command" "$@"
