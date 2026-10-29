"""Utility functions for timestamp formatting, validation, size formatting, and file cleanup."""

import os
import re
import tempfile
from pathlib import Path
from typing import Tuple, Optional, Dict, Any


def sanitize_environment() -> None:
    """
    Remove oversized environment variables that exceed Linux kernel's
    MAX_ARG_STRLEN (128 KB) or ARG_MAX limit, which causes [Errno 7] Argument list too long.
    Safely dumps any cookie strings to a temporary file before pruning from os.environ.
    """
    for key in list(os.environ.keys()):
        val = os.environ.get(key, "")
        val_len = len(val)
        # If any cookie variable is present, save it to disk and remove from env
        if "cookie" in key.lower() or key.upper() in ("YOUTUBE_COOKIES", "COOKIES"):
            if val_len > 0:
                try:
                    cfile = Path(tempfile.gettempdir()) / "env_cookies.txt"
                    if not cfile.exists() or cfile.stat().st_size == 0:
                        cfile.write_text(sanitize_cookie_content(val), encoding="utf-8")
                except Exception:
                    pass
            os.environ.pop(key, None)
            continue

        # Pop any other environment variable exceeding 8 KB (Linux MAX_ARG_STRLEN is 128 KB)
        if val_len > 8192:
            os.environ.pop(key, None)


def get_clean_env() -> Dict[str, str]:
    """Return a sanitized environment dictionary safe for Linux execve and child processes."""
    safe_keys = {
        "PATH", "LD_LIBRARY_PATH", "HOME", "USER", "LOGNAME", "SHELL",
        "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
        "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "PYTHONPATH"
    }
    clean = {
        k: v for k, v in os.environ.items()
        if (k in safe_keys or len(v) < 4096) and len(v) < 8192
    }
    if "PATH" not in clean:
        clean["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return clean


# Run immediately on module import
sanitize_environment()


def sanitize_cookie_content(content: str) -> str:
    """
    Ensure the cookie content has the proper Netscape HTTP Cookie File format for yt-dlp.
    1. Removes malformed lines that crash MozillaCookieJar.
    2. Converts spaces to tabs.
    3. If a cookie has 6 columns (value is empty), appends an empty string as the 7th column.
    4. Drops any lines with fewer than 6 columns.
    5. Preserves valid comments and ensures Netscape header.
    """
    cleaned = content.strip().strip("\"'`")
    lines = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            lines.append(line)
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            sp = re.split(r"\s+", line)
            if len(sp) >= 6:
                parts = sp
        # If line has only 6 columns (empty value), add empty value as 7th column
        if len(parts) == 6:
            parts.append("")
        if len(parts) >= 7:
            val = "\t".join(parts[6:])
            fixed = "\t".join(parts[:6] + [val])
            lines.append(fixed)
        else:
            # Skip unparsable line instead of crashing MozillaCookieJar
            continue

    has_header = any("# Netscape HTTP Cookie File" in l for l in lines[:5])
    if not has_header:
        header = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/rfc/cookie_spec.html",
            "# This is a generated file! Do not edit.",
            "",
        ]
        lines = header + lines
    return "\n".join(lines) + "\n"


def get_cookie_stats() -> Dict[str, Any]:
    """Return count and validity of active YouTube cookies for UI diagnostics."""
    cfile = get_yt_cookies_file()
    if not cfile or not Path(cfile).exists() or Path(cfile).stat().st_size == 0:
        return {"active": False, "count": 0, "names": [], "has_auth": False}
    import http.cookiejar
    cj = http.cookiejar.MozillaCookieJar(cfile)
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
        names = [c.name for c in cj]
        yt_cookies = [c for c in cj if "youtube" in getattr(c, "domain", "") or "google" in getattr(c, "domain", "")]
        auth_keys = ["SID", "HSID", "SSID", "LOGIN_INFO", "__Secure-3PSID", "SAPISID", "APISID"]
        has_auth = any(n in names for n in auth_keys)
        return {
            "active": len(cj) > 0,
            "count": len(cj),
            "yt_count": len(yt_cookies),
            "names": names[:12],
            "has_auth": has_auth,
            "file": Path(cfile).name,
        }
    except Exception as e:
        return {"active": False, "count": 0, "error": str(e), "file": Path(cfile).name}


def get_yt_cookies_file() -> Optional[str]:
    """
    Check if YouTube cookies are available from:
    1. Persistent user_cookies.txt or local cookies.txt
    2. Streamlit secrets (st.secrets["YOUTUBE_COOKIES"])
    3. Streamlit session_state (user pasted in sidebar)
    Returns the resolved string path to the cookie file or None.
    """
    # 1. Check persistent user_cookies.txt or cookies.txt on disk
    for fname in ["user_cookies.txt", "cookies.txt"]:
        p = Path(fname)
        if p.exists() and p.stat().st_size > 0:
            try:
                raw = p.read_text(encoding="utf-8")
                sanitized = sanitize_cookie_content(raw)
                p.write_text(sanitized, encoding="utf-8")
            except Exception:
                pass
            return str(p.resolve())

    # 2. Check /tmp/env_cookies.txt saved from sanitized environment variables
    env_cf = Path(tempfile.gettempdir()) / "env_cookies.txt"
    if env_cf.exists() and env_cf.stat().st_size > 0:
        try:
            raw = env_cf.read_text(encoding="utf-8")
            sanitized = sanitize_cookie_content(raw)
            env_cf.write_text(sanitized, encoding="utf-8")
            return str(env_cf.resolve())
        except Exception:
            pass

    # 3. Check Streamlit secrets (permanent cloud storage)
    try:
        import streamlit as st
        for key in ["YOUTUBE_COOKIES", "youtube_cookies", "cookies"]:
            if hasattr(st, "secrets") and key in st.secrets:
                val = st.secrets[key]
                if val and str(val).strip():
                    temp_cf = Path(tempfile.gettempdir()) / "st_secrets_cookies.txt"
                    sanitized = sanitize_cookie_content(str(val))
                    temp_cf.write_text(sanitized, encoding="utf-8")
                    return str(temp_cf.resolve())
    except Exception:
        pass

    # 3. Check Streamlit session_state
    try:
        import streamlit as st
        pasted = st.session_state.get("youtube_cookies_text")
        if pasted and str(pasted).strip():
            temp_cf = Path(tempfile.gettempdir()) / "session_cookies.txt"
            sanitized = sanitize_cookie_content(str(pasted))
            temp_cf.write_text(sanitized, encoding="utf-8")
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
