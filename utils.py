"""Utility functions for timestamp formatting, validation, size formatting, cookie handling, and file cleanup."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List


def get_ytdlp_cache_dir() -> str:
    """Return a persistent temporary cache directory for yt-dlp player JS and EJS solver libs."""
    cache_dir = Path(tempfile.gettempdir()) / "yt_dlp_cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        return str(cache_dir)
    except Exception:
        return tempfile.gettempdir()


class YdlCaptureLogger:
    """Quiet yt-dlp logger that captures warnings/errors in memory without spamming stderr."""

    def __init__(self) -> None:
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        if msg:
            self.warnings.append(str(msg))

    def error(self, msg: str) -> None:
        if msg:
            self.errors.append(str(msg))


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
                    _write_private_file(_env_cookie_path(), sanitize_cookie_content(val))
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


# ------------------------------------------------------------------------------
# Cookie storage
#
# Two kinds of cookies exist and are kept strictly apart:
#   * Session cookies: pasted by a visitor in the UI. They live only in that
#     visitor's `st.session_state`, are never written to a shared location, and
#     are never shown to anyone else.
#   * Server cookies: provided by whoever runs the app (st.secrets, a cookie
#     environment variable, or a cookies.txt / user_cookies.txt file placed next
#     to the app). Used as a fallback for every visitor, but their content is
#     never displayed or deletable from the UI.
#
# Set YT_TRIMMER_SINGLE_USER=1 when running the app only for yourself to have
# pasted cookies saved to user_cookies.txt and restored after a page refresh.
# ------------------------------------------------------------------------------
SESSION_COOKIE_KEY = "youtube_cookies_text"
SINGLE_USER_ENV = "YT_TRIMMER_SINGLE_USER"
APP_ROOT = Path(__file__).resolve().parent
SERVER_COOKIE_FILENAMES = ("user_cookies.txt", "cookies.txt")
PERSISTED_COOKIE_FILENAME = "user_cookies.txt"
_WORKING_JAR_MAX_AGE_SECONDS = 6 * 3600


def is_single_user_mode() -> bool:
    """True when the operator opted in to persisting pasted cookies on disk."""
    return os.environ.get(SINGLE_USER_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _private_cookie_dir() -> Path:
    """Owner-only (0700) directory in tempdir for working cookie jars."""
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    d = Path(tempfile.gettempdir()) / f"yt_trimmer_cookies_{uid}"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _env_cookie_path() -> Path:
    return _private_cookie_dir() / "env_cookies.txt"


def _write_private_file(path: Path, content: str) -> None:
    """Atomically write `content` to `path` with owner-only (0600) permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _working_jar_path(sanitized_content: str) -> Path:
    digest = hashlib.sha256(sanitized_content.encode("utf-8")).hexdigest()[:32]
    return _private_cookie_dir() / f"jar_{digest}.txt"


def _prune_stale_working_jars() -> None:
    try:
        cutoff = time.time() - _WORKING_JAR_MAX_AGE_SECONDS
        for jar in _private_cookie_dir().glob("jar_*.txt"):
            try:
                if jar.stat().st_mtime < cutoff:
                    jar.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _write_working_cookie_copy(sanitized_content: str) -> str:
    """
    Write sanitized cookie content to a working jar and return its path.

    yt-dlp mutates/overwrites its `cookiefile` in place after every request, so it
    never gets a master file. Each distinct cookie set gets its own owner-only jar
    named by content hash, so concurrent sessions in one process never share a jar
    with different cookies, and the jar is reset from the master on every call.
    """
    _prune_stale_working_jars()
    jar = _working_jar_path(sanitized_content)
    _write_private_file(jar, sanitized_content)
    return str(jar)


def _read_session_cookies() -> Optional[str]:
    """Return the current visitor's pasted cookies, if any."""
    try:
        import streamlit as st
        pasted = st.session_state.get(SESSION_COOKIE_KEY)
        if pasted and str(pasted).strip():
            return str(pasted)
    except Exception:
        pass
    return None


def _read_server_cookies() -> Optional[str]:
    """Return operator-provided cookies (files next to the app, env var, or st.secrets)."""
    for name in SERVER_COOKIE_FILENAMES:
        p = APP_ROOT / name
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p.read_text(encoding="utf-8")
        except Exception:
            continue

    try:
        env_cf = _env_cookie_path()
        if env_cf.is_file() and env_cf.stat().st_size > 0:
            return env_cf.read_text(encoding="utf-8")
    except Exception:
        pass

    try:
        import streamlit as st
        for key in ("YOUTUBE_COOKIES", "youtube_cookies", "cookies"):
            if key in st.secrets:
                val = st.secrets[key]
                if val and str(val).strip():
                    return str(val)
    except Exception:
        pass
    return None


def _resolve_cookies() -> Tuple[Optional[str], Optional[str]]:
    """Return (sanitized cookie content, source) where source is 'session' or 'server'."""
    for source, reader in (("session", _read_session_cookies), ("server", _read_server_cookies)):
        raw = reader()
        if raw:
            sanitized = sanitize_cookie_content(raw)
            if any(l.strip() and not l.startswith("#") for l in sanitized.splitlines()):
                return sanitized, source
    return None, None


def save_yt_cookies(content: str) -> str:
    """
    Sanitize pasted cookies and return them for storing in the visitor's session.

    Nothing is written to disk unless YT_TRIMMER_SINGLE_USER is enabled, in which
    case the cookies are also saved (0600) to user_cookies.txt to survive refreshes.
    """
    sanitized = sanitize_cookie_content(content)
    if is_single_user_mode():
        try:
            _write_private_file(APP_ROOT / PERSISTED_COOKIE_FILENAME, sanitized)
        except Exception:
            pass
    return sanitized


def load_persisted_cookies() -> str:
    """Return cookies saved by single-user mode, or '' (always '' in multi-user mode)."""
    if not is_single_user_mode():
        return ""
    p = APP_ROOT / PERSISTED_COOKIE_FILENAME
    try:
        if p.is_file() and p.stat().st_size > 0:
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def clear_yt_cookies(session_content: Optional[str] = None) -> None:
    """
    Remove the calling visitor's cookies: their working jar and, in single-user
    mode, the persisted user_cookies.txt. Server cookies and other visitors'
    jars are never touched.
    """
    if session_content and str(session_content).strip():
        try:
            _working_jar_path(sanitize_cookie_content(str(session_content))).unlink(missing_ok=True)
        except Exception:
            pass
    if is_single_user_mode():
        try:
            (APP_ROOT / PERSISTED_COOKIE_FILENAME).unlink(missing_ok=True)
        except Exception:
            pass


def get_yt_cookies_file() -> Optional[str]:
    """
    Return the path to an isolated working cookie jar for yt-dlp, or None.

    The visitor's own pasted cookies (session) take priority; otherwise
    operator-provided server cookies are used.
    """
    sanitized, _source = _resolve_cookies()
    if not sanitized:
        return None
    try:
        return _write_working_cookie_copy(sanitized)
    except Exception:
        return None


def get_cookie_stats() -> Dict[str, Any]:
    """Return count and validity of active YouTube cookies for UI diagnostics."""
    content, source = _resolve_cookies()
    cfile = None
    if content:
        try:
            cfile = _write_working_cookie_copy(content)
        except Exception:
            cfile = None
    if not cfile or not Path(cfile).exists() or Path(cfile).stat().st_size == 0:
        return {"active": False, "count": 0, "names": [], "has_auth": False, "source": None}
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
            "source": source,
        }
    except Exception as e:
        return {"active": False, "count": 0, "error": str(e), "source": source}


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


# Run on module import (after the cookie helpers it depends on are defined)
sanitize_environment()
