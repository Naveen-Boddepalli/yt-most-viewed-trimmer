"""Utility functions for timestamp formatting, validation, size formatting, cookie handling, and file cleanup."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Tuple, Optional, Dict, Any


def get_js_runtimes() -> Dict[str, Dict[str, str]]:
    """
    Locate installed JavaScript runtimes (Node.js / Deno) for yt-dlp's EJS
    signature & n-challenge solver. Returns a dict suitable for `ydl_opts['js_runtimes']`.
    """
    runtimes: Dict[str, Dict[str, str]] = {}

    node_candidates = [
        shutil.which("node"),
        shutil.which("nodejs"),
        "/usr/bin/node",
        "/usr/bin/nodejs",
        "/usr/local/bin/node",
        "/opt/homebrew/bin/node",
    ]
    for cand in node_candidates:
        if cand and Path(cand).is_file():
            runtimes["node"] = {"path": str(cand)}
            break
    if "node" not in runtimes:
        runtimes["node"] = {}

    deno_candidates = []
    try:
        import deno as deno_pkg
        deno_bin = deno_pkg.find_deno_bin()
        if deno_bin:
            deno_candidates.append(deno_bin)
    except Exception:
        pass
    deno_candidates.extend([
        shutil.which("deno"),
        "/usr/local/bin/deno",
        "/opt/homebrew/bin/deno",
        str(Path.home() / ".deno" / "bin" / "deno"),
    ])
    for cand in deno_candidates:
        if cand and Path(cand).is_file():
            runtimes["deno"] = {"path": str(cand)}
            break
    if "deno" not in runtimes:
        runtimes["deno"] = {}

    return runtimes


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


def _parse_cookie_expiry(raw_exp: Any) -> int:
    try:
        val = int(float(raw_exp))
        return val if val >= 0 else 1999999999
    except Exception:
        return 1999999999


def sanitize_cookie_content(content: str) -> str:
    """
    Ensure the cookie content has the proper Netscape HTTP Cookie File format for yt-dlp.
    Supports:
    1. Netscape format (standard and #HttpOnly_ prefixes)
    2. JSON cookie arrays (Cookie-Editor / EditThisCookie)
    3. Chrome/Edge DevTools multi-column table copy-paste
    4. Semicolon-separated key=value strings (Cookie header or document.cookie)
    5. Two-column name/value lines or alternating Name/Value lines
    """
    cleaned = content.strip().strip("\"'`")
    lines = []

    # 1. JSON cookie array
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            cookie_list = json.loads(cleaned)
            if isinstance(cookie_list, list):
                for item in cookie_list:
                    if not isinstance(item, dict):
                        continue
                    domain = str(item.get("domain", ".youtube.com")).strip() or ".youtube.com"
                    include_sub = "TRUE" if domain.startswith(".") else "FALSE"
                    path = str(item.get("path", "/")).strip() or "/"
                    secure = "TRUE" if item.get("secure", True) else "FALSE"
                    exp = _parse_cookie_expiry(item.get("expirationDate") or item.get("expiry"))
                    name = str(item.get("name", "")).strip()
                    val = str(item.get("value", "")).strip()
                    if name:
                        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{exp}\t{name}\t{val}")
        except Exception:
            lines = []

    # 2. Netscape format (strict field validation so DevTools rows aren't misread)
    if not lines:
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                # Keep HttpOnly_ lines by stripping the #HttpOnly_ prefix so MozillaCookieJar reads them
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                else:
                    lines.append(line)
                    continue
            parts = line.split("\t")
            if len(parts) < 6:
                sp = re.split(r"\s+", line)
                if len(sp) >= 6:
                    parts = sp
            if len(parts) == 6:
                parts.append("")
            if (
                len(parts) >= 7
                and parts[1].upper() in ("TRUE", "FALSE")
                and parts[3].upper() in ("TRUE", "FALSE")
                and re.match(r"^\d+(?:\.\d+)?$", parts[4])
            ):
                exp_int = str(_parse_cookie_expiry(parts[4]))
                val = "\t".join(parts[6:])
                fixed = "\t".join([parts[0], parts[1].upper(), parts[2], parts[3].upper(), exp_int, parts[5], val])
                lines.append(fixed)
            elif (
                len(parts) >= 4
                and re.match(r"^[A-Za-z0-9_\-\.\:\+]+$", parts[0])
                and ("youtube.com" in parts[2] or "google.com" in parts[2])
            ):
                # Chrome/Edge DevTools Cookies table row: name, value, domain, path, ...
                domain = parts[2].strip() or ".youtube.com"
                include_sub = "TRUE" if domain.startswith(".") else "FALSE"
                path = (parts[3].strip() if len(parts) > 3 else "/") or "/"
                if not path.startswith("/"):
                    path = "/"
                lines.append(f"{domain}\t{include_sub}\t{path}\tTRUE\t1999999999\t{parts[0].strip()}\t{parts[1].strip()}")

    # 3. Fallback: semicolon-separated key=value pairs (Cookie header or document.cookie)
    cookie_entries = [l for l in lines if not l.startswith("#")]
    if not cookie_entries and ";" in cleaned and "=" in cleaned:
        cleaned_no_hdr = re.sub(r"^[Cc]ookie:\s*", "", cleaned)
        for pair in cleaned_no_hdr.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and re.match(r"^[A-Za-z0-9_\-\.\:\+]+$", k):
                    lines.append(f".youtube.com\tTRUE\t/\tTRUE\t1999999999\t{k}\t{v}")

    # 4. Fallback: 2-column or DevTools alternating lines (Name then Value)
    cookie_entries = [l for l in lines if not l.startswith("#")]
    if not cookie_entries:
        raw_non_empty = [l.strip() for l in cleaned.splitlines() if l.strip() and not l.strip().startswith("#")]
        for rline in raw_non_empty:
            parts = re.split(r"[\t\s]+", rline, maxsplit=1)
            if len(parts) == 2 and re.match(r"^[A-Za-z0-9_\-\.\:\+]+$", parts[0]):
                lines.append(f".youtube.com\tTRUE\t/\tTRUE\t1999999999\t{parts[0]}\t{parts[1]}")
        cookie_entries = [l for l in lines if not l.startswith("#")]
        if not cookie_entries and len(raw_non_empty) >= 2 and len(raw_non_empty) % 2 == 0:
            for i in range(0, len(raw_non_empty), 2):
                k = raw_non_empty[i]
                v = raw_non_empty[i + 1]
                if re.match(r"^[A-Za-z0-9_\-\.\:\+]+$", k):
                    lines.append(f".youtube.com\tTRUE\t/\tTRUE\t1999999999\t{k}\t{v}")

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


def _write_working_cookie_copy(sanitized_content: str) -> str:
    """
    Write sanitized cookie content to a fresh working file in tempdir.
    yt-dlp mutates/overwrites its `cookiefile` in-place after every request,
    which can strip or expire master cookies if given the master file directly.
    """
    work_cf = Path(tempfile.gettempdir()) / f"yt_working_cookies_{os.getpid()}.txt"
    work_cf.write_text(sanitized_content, encoding="utf-8")
    return str(work_cf.resolve())


def save_yt_cookies(content: str) -> str:
    """Sanitize and persist user cookies to disk (CWD with tempdir fallback)."""
    sanitized = sanitize_cookie_content(content)
    for target in (Path("user_cookies.txt"), Path(tempfile.gettempdir()) / "user_cookies.txt"):
        try:
            target.write_text(sanitized, encoding="utf-8")
            return sanitized
        except Exception:
            continue
    return sanitized


def clear_yt_cookies() -> None:
    """Remove persisted and working cookie files from disk and tempdir."""
    for p in (
        Path("user_cookies.txt"),
        Path("cookies.txt"),
        Path(tempfile.gettempdir()) / "user_cookies.txt",
        Path(tempfile.gettempdir()) / "env_cookies.txt",
    ):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    for tmp_f in Path(tempfile.gettempdir()).glob("*cookies*.txt"):
        try:
            tmp_f.unlink()
        except Exception:
            pass


def get_yt_cookies_file() -> Optional[str]:
    """
    Check if YouTube cookies are available from:
    1. Persistent user_cookies.txt or local cookies.txt (CWD or tempdir)
    2. /tmp/env_cookies.txt
    3. Streamlit secrets (st.secrets["YOUTUBE_COOKIES"])
    4. Streamlit session_state (user pasted in sidebar)
    Returns the resolved string path to an isolated working cookie file or None.
    """
    # 1. Check persistent user_cookies.txt or cookies.txt on disk
    disk_candidates = [
        Path("user_cookies.txt"),
        Path("cookies.txt"),
        Path(tempfile.gettempdir()) / "user_cookies.txt",
    ]
    for p in disk_candidates:
        if p.exists() and p.stat().st_size > 0:
            try:
                raw = p.read_text(encoding="utf-8")
                sanitized = sanitize_cookie_content(raw)
                return _write_working_cookie_copy(sanitized)
            except Exception:
                return str(p.resolve())

    # 2. Check /tmp/env_cookies.txt saved from sanitized environment variables
    env_cf = Path(tempfile.gettempdir()) / "env_cookies.txt"
    if env_cf.exists() and env_cf.stat().st_size > 0:
        try:
            raw = env_cf.read_text(encoding="utf-8")
            sanitized = sanitize_cookie_content(raw)
            return _write_working_cookie_copy(sanitized)
        except Exception:
            pass

    # 3. Check Streamlit secrets (permanent cloud storage)
    try:
        import streamlit as st
        for key in ["YOUTUBE_COOKIES", "youtube_cookies", "cookies"]:
            if hasattr(st, "secrets") and key in st.secrets:
                val = st.secrets[key]
                if val and str(val).strip():
                    sanitized = sanitize_cookie_content(str(val))
                    return _write_working_cookie_copy(sanitized)
    except Exception:
        pass

    # 4. Check Streamlit session_state
    try:
        import streamlit as st
        pasted = st.session_state.get("youtube_cookies_text")
        if pasted and str(pasted).strip():
            sanitized = sanitize_cookie_content(str(pasted))
            return _write_working_cookie_copy(sanitized)
    except Exception:
        pass

    return None


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
        yt_cookies = [c for c in cj if "youtube.com" in getattr(c, "domain", "")]
        yt_names = {c.name for c in yt_cookies}
        # True YouTube session authentication requires LOGIN_INFO, __Secure-1PSID, __Secure-3PSID, or SAPISID on .youtube.com
        has_auth = bool({"LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID", "SAPISID"} & yt_names)
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
