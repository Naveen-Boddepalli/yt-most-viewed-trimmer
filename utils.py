"""Utility functions for timestamp formatting, validation, size formatting, and file cleanup."""

import os
import re
import tempfile
from pathlib import Path
from typing import Tuple, Optional


def get_yt_cookies_file() -> Optional[str]:
    """
    Check if YouTube cookies are available from:
    1. Local cookies.txt file
    2. Streamlit session_state (user pasted in sidebar)
    3. Streamlit secrets (st.secrets["YOUTUBE_COOKIES"])
    Returns the resolved string path to the cookie file or None.
    """
    # 1. Local cookies.txt file in current directory or workspace
    local_cookie = Path("cookies.txt")
    if local_cookie.exists() and local_cookie.stat().st_size > 0:
        return str(local_cookie.resolve())

    # 2. Check Streamlit session_state
    try:
        import streamlit as st
        pasted = st.session_state.get("youtube_cookies_text")
        if pasted and str(pasted).strip():
            ws_dir = st.session_state.get("workspace_dir") or tempfile.gettempdir()
            temp_cf = Path(ws_dir) / "session_cookies.txt"
            temp_cf.write_text(str(pasted).strip(), encoding="utf-8")
            return str(temp_cf.resolve())
    except Exception:
        pass

    # 3. Check Streamlit secrets
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "YOUTUBE_COOKIES" in st.secrets:
            secret_cookies = st.secrets["YOUTUBE_COOKIES"]
            if secret_cookies and str(secret_cookies).strip():
                temp_cf = Path(tempfile.gettempdir()) / "st_secrets_cookies.txt"
                temp_cf.write_text(str(secret_cookies).strip(), encoding="utf-8")
                return str(temp_cf.resolve())
    except Exception:
        pass

    return None


def format_bytes(size_bytes: Optional[float]) -> str:
    """
    Convert a byte count into a human-readable string (B, KB, MB, GB).
    Handles None and negative values gracefully.
    """
    if size_bytes is None or size_bytes < 0:
        return "Unknown"
    size = float(size_bytes)
    if size < 1024:
        return f"{int(size)} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (e.g. terminal color codes) from a string."""
    if not text:
        return ""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", str(text))


def format_timestamp(seconds: float) -> str:
    """Convert seconds (float) to MM:SS or HH:MM:SS string format."""
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timestamp(ts_str: str) -> float:
    """Convert MM:SS or HH:MM:SS string into float seconds."""
    parts = [float(p.strip()) for p in ts_str.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 1:
        return parts[0]
    raise ValueError(f"Invalid timestamp format: {ts_str}")


def is_valid_youtube_url(url: str) -> bool:
    """Validate whether a string is a valid YouTube URL."""
    if not url or not isinstance(url, str):
        return False
    pattern = re.compile(
        r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/|live/)|youtu\.be/)[\w\-]{11}.*$"
    )
    return bool(pattern.match(url.strip()))


def sanitize_filename(name: str) -> str:
    """Remove unsafe filesystem characters from a string."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:80] or "youtube_video"
