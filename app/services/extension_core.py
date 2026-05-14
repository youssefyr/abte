# app/services/extension_core.py
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QStandardPaths

logger = logging.getLogger(__name__)


class ExtensionCoreHandler:
    """
    Singleton-friendly owner of:
      - The shared secret token (auth + encryption key for the bridge)
      - The native messaging bridge script generation and installation
      - The OS-level manifest registration (Firefox, Chromium families)
      - Extension state file reads (push_state payloads from background.js)
      - Push commands to the extension (push_block, push_unblock, push_task)

    Both ActiveWindowService and TabFocusGuard hold a reference to the
    same instance. Construct once in bootstrap, pass everywhere.
    """

    CACHE_TTL_SECONDS = 0.5
    EXTENSION_ID = "abte-bridge@zyroo.com"
    HOST_NAME = "com.zyroo.abte"
    STATE_TTL_SECONDS = 5.0

    # Bump this version string whenever the bridge script logic changes.
    # The installer uses it to detect stale bridges and force re-registration.
    BRIDGE_VERSION = "1.4.0"  # bumped: task_file bootstrap + per-tab blocking support

    def __init__(self) -> None:
        self._app_data_dir = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
            / "abte"
        )
        self._app_data_dir.mkdir(parents=True, exist_ok=True)

        self._token = self._ensure_secure_token()
        self._install_native_messaging_host()

        self._state_file = self._app_data_dir / "extension_state.json"
        self._cmd_file = self._app_data_dir / "extension_cmd.json"

        self._cached_payload: Optional[dict] = None
        self._last_fetch_time = 0.0

    # -----------------------------------------------------------------------
    # Token
    # -----------------------------------------------------------------------

    def token(self) -> str:
        return self._token

    def _ensure_secure_token(self) -> str:
        token_file = self._app_data_dir / "extension_auth.token"
        if token_file.exists():
            try:
                return token_file.read_text(encoding="utf-8").strip()
            except Exception as exc:
                logger.warning(f"ExtensionCoreHandler: failed to read token: {exc}")

        new_token = secrets.token_hex(32)
        try:
            token_file.write_text(new_token, encoding="utf-8")
            if sys.platform != "win32":
                os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as exc:
            logger.error(f"ExtensionCoreHandler: failed to write token: {exc}")
        return new_token

    # -----------------------------------------------------------------------
    # State reads (called by ActiveWindowService / TabFocusGuard)
    # -----------------------------------------------------------------------

    def fetch_raw_payload(self) -> Optional[dict]:
        """
        Returns the latest decrypted push_state payload written by the bridge,
        or None if stale / absent. Cached for CACHE_TTL_SECONDS.
        """
        now = time.time()
        if self._cached_payload and (now - self._last_fetch_time) < self.CACHE_TTL_SECONDS:
            return self._cached_payload

        if not self._state_file.exists():
            return None

        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            received_at = float(payload.get("received_at", 0.0))
            if received_at <= 0 or (now - received_at) > self.STATE_TTL_SECONDS:
                return None
            self._cached_payload = payload
            self._last_fetch_time = now
            return payload
        except Exception as exc:
            logger.debug(f"ExtensionCoreHandler: failed to read state: {exc}")
            return None

    def invalidate_cache(self) -> None:
        self._cached_payload = None
        self._last_fetch_time = 0.0

    # -----------------------------------------------------------------------
    # Push commands to extension (block / unblock / task)
    # -----------------------------------------------------------------------

    def push_block(self, reason: str = "off_task") -> None:
        self._write_cmd({"cmd": "block", "reason": reason, "ts": time.time()})

    def push_unblock(self) -> None:
        self._write_cmd({"cmd": "unblock", "ts": time.time()})

    def push_task(self, task_title: str, keywords: list[str]) -> None:
        self._write_cmd({
            "cmd": "set_task",
            "task_title": task_title,
            "task_keywords": keywords,
            "ts": time.time(),
        })

    def push_clear_task(self) -> None:
        self._write_cmd({"cmd": "clear_task", "ts": time.time()})

    def _write_cmd(self, payload: dict) -> None:
        try:
            self._cmd_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            logger.debug(f"ExtensionCoreHandler: failed to write cmd: {exc}")

    def cmd_file_path(self) -> Path:
        return self._cmd_file

    # -----------------------------------------------------------------------
    # Native messaging bridge installation
    # -----------------------------------------------------------------------

    def _build_bridge_script(self) -> str:
        """
        Returns the full bridge script source. Embed BRIDGE_VERSION so the
        version stamp file can detect whether a re-write is needed.
        """
        return f"""#!/usr/bin/env python3
# abte_bridge.py  version={self.BRIDGE_VERSION}
import base64, hashlib, hmac, json, os, struct, sys, time
from pathlib import Path

def get_message():
    raw = sys.stdin.buffer.read(4)
    if not raw: sys.exit(0)
    msg_length = struct.unpack('@I', raw)[0]
    payload = sys.stdin.buffer.read(msg_length)
    if not payload: sys.exit(0)
    return json.loads(payload.decode('utf-8'))

def send_message(msg):
    encoded = json.dumps(msg).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('@I', len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()

def _xor_stream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        digest = hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
        out.extend(digest)
        counter += 1
    return bytes(out[:length])

def _decrypt_payload(token_hex, nonce_b64, payload_b64, sig_b64):
    key = bytes.fromhex(token_hex)
    nonce = base64.b64decode(nonce_b64)
    cipher = base64.b64decode(payload_b64)
    signature = base64.b64decode(sig_b64)
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('invalid_signature')
    stream = _xor_stream(key, nonce, len(cipher))
    return json.loads(bytes(c ^ s for c, s in zip(cipher, stream)).decode('utf-8'))

def main():
    app_dir    = Path(__file__).parent
    token_file = app_dir / "extension_auth.token"
    state_file = app_dir / "extension_state.json"
    cmd_file   = app_dir / "extension_cmd.json"
    task_file  = app_dir / "extension_task.json"

    while True:
        try:
            msg = get_message()
            command = msg.get("command")

            if command == "get_token":
                token = token_file.read_text().strip() if token_file.exists() else None
                current_task = None
                if task_file.exists():
                    try:
                        current_task = json.loads(task_file.read_text(encoding="utf-8"))
                    except Exception:
                        current_task = None
                send_message({{"token": token, "current_task": current_task}})

            elif command == "push_state":
                if not token_file.exists():
                    send_message({{"ok": False, "error": "missing_token"}}); continue
                token = token_file.read_text().strip()
                data = _decrypt_payload(
                    token,
                    msg.get("nonce", ""),
                    msg.get("payload", ""),
                    msg.get("hmac", ""),
                )
                data["received_at"] = time.time()
                state_file.write_text(json.dumps(data), encoding="utf-8")
                send_message({{"ok": True}})

            elif command == "poll_cmd":
                if cmd_file.exists():
                    try:
                        payload = json.loads(cmd_file.read_text(encoding="utf-8"))
                        cmd_file.unlink(missing_ok=True)
                        if payload.get("cmd") == "set_task":
                            task_file.write_text(json.dumps({{
                                "task_title":    payload.get("task_title", ""),
                                "task_keywords": payload.get("task_keywords", []),
                            }}), encoding="utf-8")
                        elif payload.get("cmd") == "clear_task":
                            task_file.unlink(missing_ok=True)
                        send_message({{"cmd_payload": payload}})
                    except Exception:
                        send_message({{"cmd_payload": None}})
                else:
                    send_message({{"cmd_payload": None}})

            else:
                send_message({{"ok": False, "error": "unknown_command"}})

        except Exception:
            send_message({{"ok": False, "error": "bridge_failure"}})

if __name__ == '__main__':
    main()
"""

    def _install_native_messaging_host(self) -> None:
        bridge_path = self._app_data_dir / "abte_bridge.py"
        manifest_path = self._app_data_dir / f"{self.HOST_NAME}.json"
        version_stamp = self._app_data_dir / "abte_bridge.version"

        new_script = self._build_bridge_script()
        new_hash = hashlib.sha256(new_script.encode("utf-8")).hexdigest()

        # Determine whether the on-disk bridge is already up-to-date.
        # We compare both the version string and a content hash so that
        # in-place edits to this file are picked up even without a version bump.
        needs_update = True
        if bridge_path.exists() and version_stamp.exists():
            try:
                stamp = json.loads(version_stamp.read_text(encoding="utf-8"))
                if (
                    stamp.get("version") == self.BRIDGE_VERSION
                    and stamp.get("sha256") == new_hash
                ):
                    needs_update = False
            except Exception:
                pass

        if needs_update:
            logger.info(
                f"ExtensionCoreHandler: writing bridge v{self.BRIDGE_VERSION} "
                f"(hash {new_hash[:12]}…)"
            )
            bridge_path.write_text(new_script, encoding="utf-8")

            if sys.platform != "win32":
                st = os.stat(bridge_path)
                os.chmod(bridge_path, st.st_mode | stat.S_IEXEC)

            # Persist the manifest on every update so the absolute path stays current
            # (handles app being moved or reinstalled to a different directory).
            manifest = {
                "name": self.HOST_NAME,
                "description": "Bridge for Abte Secure Token",
                "path": str(bridge_path.absolute()),
                "type": "stdio",
                "allowed_extensions": [self.EXTENSION_ID],
            }
            if sys.platform == "win32":
                bat_path = bridge_path.with_suffix(".bat")
                bat_path.write_text(f'@echo off\r\npython "{bridge_path.absolute()}"\r\n', encoding="utf-8")
                manifest["path"] = str(bat_path.absolute())
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self._register_manifest_with_browsers(manifest_path)

            # Write the version stamp last — so a crash mid-write leaves
            # needs_update=True on the next run and retries cleanly.
            version_stamp.write_text(
                json.dumps({"version": self.BRIDGE_VERSION, "sha256": new_hash}),
                encoding="utf-8",
            )
        else:
            logger.debug(
                f"ExtensionCoreHandler: bridge v{self.BRIDGE_VERSION} already up-to-date, skipping rewrite."
            )

    def _register_manifest_with_browsers(self, manifest_path: Path) -> None:
        if sys.platform.startswith("linux"):
            ff_dir = Path.home() / ".mozilla/native-messaging-hosts"
            ff_dir.mkdir(parents=True, exist_ok=True)
            self._safe_symlink(manifest_path, ff_dir / f"{self.HOST_NAME}.json")

            for chrome_dir in [
                Path.home() / ".config/google-chrome/NativeMessagingHosts",
                Path.home() / ".config/chromium/NativeMessagingHosts",
                Path.home() / ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
                Path.home() / ".config/microsoft-edge/NativeMessagingHosts",
                Path.home() / ".config/vivaldi/NativeMessagingHosts",
            ]:
                try:
                    chrome_dir.mkdir(parents=True, exist_ok=True)
                    self._safe_symlink(manifest_path, chrome_dir / f"{self.HOST_NAME}.json")
                except Exception:
                    pass

        elif sys.platform == "win32":
            import winreg
            try:
                key_path = rf"Software\Mozilla\NativeMessagingHosts\{self.HOST_NAME}"
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path.absolute()))
            except Exception as exc:
                logger.error(f"ExtensionCoreHandler: registry write failed: {exc}")

    @staticmethod
    def _safe_symlink(src: Path, dst: Path) -> None:
        try:
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            dst.symlink_to(src)
        except Exception as exc:
            logger.error(f"ExtensionCoreHandler: symlink failed {dst}: {exc}")