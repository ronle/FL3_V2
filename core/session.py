"""
FL3 Session Manager — Centralized logging & session continuity

Every CLI script should start with:
    from core.session import Session
    session = Session.resume_or_create("my_script_name")

This guarantees:
- Append-only logs across invocations (no new file per run)
- A .current_session pointer so new CLIs auto-inherit context
- Structured JSON log entries + human-readable stdout
- Session manifest for Claude to read across conversations
"""

import os
import json
import sys
import time
import traceback
import functools
from datetime import datetime
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent.parent
LOGS_DIR      = PROJECT_ROOT / "logs"
MANIFEST_FILE = LOGS_DIR / "sessions.json"
CURRENT_FILE  = PROJECT_ROOT / ".current_session"   # active session pointer


class Session:
    """
    Persistent session logger. Append-only across multiple CLI invocations.
    """

    def __init__(self, name: str, log_path: Path, session_id: str, is_new: bool):
        self.name       = name
        self.log_path   = log_path
        self.session_id = session_id
        self.is_new     = is_new
        self._start_time = time.time()

        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Write .current_session pointer
        CURRENT_FILE.write_text(json.dumps({
            "name":       name,
            "session_id": session_id,
            "log_path":   str(log_path),
            "pid":        os.getpid(),
            "started_at": datetime.now().isoformat(),
        }, indent=2))

        action = "CREATED" if is_new else "RESUMED"
        self._write(f"=== SESSION {action}: {name} | id={session_id} | pid={os.getpid()} ===")

    # ── Factory methods ──────────────────────────────────────────────

    @classmethod
    def resume_or_create(cls, name: str) -> "Session":
        """
        Main entry point. Resumes an active session for `name`,
        or creates a new one if none exists or the last one is closed.
        """
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        manifest = cls._load_manifest()

        existing = manifest.get(name)
        # Resume if active OR if same-day session exists (append across invocations)
        same_day = (existing and
                    existing.get("created_at", "")[:10] == datetime.now().strftime("%Y-%m-%d"))
        if existing and (existing.get("status") == "active" or same_day):
            log_path   = Path(existing["log_path"])
            session_id = existing["session_id"]
            is_new     = False
            # Re-mark as active if it was closed
            if existing.get("status") != "active":
                manifest[name]["status"] = "active"
                cls._save_manifest(manifest)
        else:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path   = LOGS_DIR / f"{name}_{session_id}.log"
            is_new     = True
            manifest[name] = {
                "name":       name,
                "session_id": session_id,
                "log_path":   str(log_path),
                "status":     "active",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            cls._save_manifest(manifest)

        return cls(name, log_path, session_id, is_new)

    @classmethod
    def new(cls, name: str) -> "Session":
        """Force-create a new session, closing the previous one."""
        manifest = cls._load_manifest()
        if name in manifest:
            manifest[name]["status"] = "closed"
            manifest[name]["closed_at"] = datetime.now().isoformat()
        manifest.pop(name, None)
        cls._save_manifest(manifest)
        return cls.resume_or_create(name)

    @classmethod
    def from_current(cls) -> "Session":
        """Resume whatever session is currently active (reads .current_session)."""
        if not CURRENT_FILE.exists():
            raise RuntimeError("No active session. Run Session.resume_or_create('name') first.")
        info = json.loads(CURRENT_FILE.read_text())
        return cls.resume_or_create(info["name"])

    # ── Logging methods ──────────────────────────────────────────────

    def log(self, message: str, level: str = "INFO", data: dict = None):
        """Write a structured log entry."""
        entry = {
            "ts":      datetime.now().isoformat(),
            "level":   level,
            "session": self.name,
            "msg":     message,
        }
        if data:
            entry["data"] = data

        self._write(json.dumps(entry))
        self._print(level, message, data)
        self._update_manifest_timestamp()

    def info(self, message: str, data: dict = None):
        self.log(message, "INFO", data)

    def warn(self, message: str, data: dict = None):
        self.log(message, "WARN", data)

    def error(self, message: str, data: dict = None):
        self.log(message, "ERROR", data)

    def result(self, label: str, value, data: dict = None):
        """Log a named result/metric — easy for Claude to grep."""
        payload = {"label": label, "value": value}
        if data:
            payload.update(data)
        self.log(f"RESULT: {label} = {value}", "RESULT", payload)

    def section(self, title: str):
        """Visual separator in the log."""
        self._write(f"\n{'─'*60}\n  {title}\n{'─'*60}")
        print(f"\n{'─'*60}\n  {title}\n{'─'*60}")

    def close(self, status: str = "completed"):
        """Mark session as closed."""
        elapsed = time.time() - self._start_time
        self.log(f"Session closed. Status={status} elapsed={elapsed:.1f}s")
        self._write(f"=== SESSION CLOSED: {self.name} | {status} ===\n")

        manifest = self._load_manifest()
        if self.name in manifest:
            manifest[self.name]["status"]    = status
            manifest[self.name]["closed_at"] = datetime.now().isoformat()
            self._save_manifest(manifest)

        if CURRENT_FILE.exists():
            CURRENT_FILE.unlink()

    # ── Context manager ──────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.error(f"Unhandled exception: {exc_val}",
                       {"traceback": traceback.format_exc()})
            self.close("failed")
        else:
            self.close("completed")
        return False

    # ── Decorator ────────────────────────────────────────────────────

    @staticmethod
    def tracked(session_name: str):
        """
        Decorator — wrap any function to auto-create/resume a session.

        Usage:
            @Session.tracked("fl3_backtest")
            def main():
                ...
        """
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                with Session.resume_or_create(session_name) as s:
                    try:
                        import inspect
                        sig = inspect.signature(fn)
                        if "session" in sig.parameters:
                            return fn(*args, session=s, **kwargs)
                    except Exception:
                        pass
                    return fn(*args, **kwargs)
            return wrapper
        return decorator

    # ── Internal helpers ─────────────────────────────────────────────

    def _write(self, text: str):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _print(self, level: str, message: str, data: dict = None):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "", "WARN": "\033[33m", "ERROR": "\033[31m",
                  "RESULT": "\033[32m"}
        reset  = "\033[0m"
        color  = colors.get(level, "")
        print(f"{color}[{ts}] [{level}] {message}{reset}")
        if data and level in ("RESULT", "ERROR"):
            print(f"         {json.dumps(data, default=str)}")

    def _update_manifest_timestamp(self):
        manifest = self._load_manifest()
        if self.name in manifest:
            manifest[self.name]["updated_at"] = datetime.now().isoformat()
            self._save_manifest(manifest)

    @staticmethod
    def _load_manifest() -> dict:
        if MANIFEST_FILE.exists():
            try:
                return json.loads(MANIFEST_FILE.read_text())
            except Exception:
                return {}
        return {}

    @staticmethod
    def _save_manifest(manifest: dict):
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2))
