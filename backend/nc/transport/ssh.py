"""SSH transport (Netmiko) with host-key pinning (trust on first use, explicit user approval)."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import socket

import paramiko

from nc.core.errors import TransportError


def fetch_host_key(host: str, port: int, timeout: float = 8.0) -> dict:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise TransportError(f"Cannot reach {host}:{port} ({e.__class__.__name__}: {e})", "unreachable") from e
    t = paramiko.Transport(sock)
    try:
        t.start_client(timeout=timeout)
        key = t.get_remote_server_key()
        banner = t.remote_version
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        return {"type": key.get_name(), "fingerprint": fp, "banner": banner}
    except Exception as e:  # noqa: BLE001
        raise TransportError(f"SSH negotiation failed: {e}", "ssh_negotiation") from e
    finally:
        t.close()


def suggest_vendor(banner: str | None) -> str | None:
    """Heuristic only; the real identification is done from command output."""
    b = (banner or "").lower()
    if "ros" in b and "ssh" in b and "openssh" not in b:
        return "mikrotik"
    if "cisco" in b:
        return "cisco"
    return None


def _load_pkey(pem: str):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem))
        except Exception:  # noqa: BLE001
            continue
    raise TransportError("The private key could not be read (unsupported format or passphrase-protected).", "bad_key")


class SshTransport:
    def __init__(self, host, port, username, secrets: dict, netmiko_type: str, pinned_fp: str | None, timeout: int = 20):
        self.host, self.port, self.username, self.secrets = host, port, username, secrets
        self.device_type, self.pinned_fp, self.timeout = netmiko_type, pinned_fp, timeout
        self._conn = None

    def connect(self) -> None:
        from netmiko import ConnectHandler
        from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

        if not self.pinned_fp:
            raise TransportError("The SSH host key has not been trusted yet.", "host_key_untrusted",
                                 "Open the device, review the fingerprint and trust it.")
        live = fetch_host_key(self.host, self.port, 8)
        if live["fingerprint"] != self.pinned_fp:
            raise TransportError(f"SSH host key CHANGED (expected {self.pinned_fp}, got {live['fingerprint']}). "
                                 "Connection refused: possible man-in-the-middle or device replacement.",
                                 "host_key_changed", "If the device was replaced, re-trust the new key.")
        params = dict(device_type=self.device_type, host=self.host, port=self.port, username=self.username,
                      conn_timeout=self.timeout, auth_timeout=self.timeout, banner_timeout=self.timeout, fast_cli=False)
        if self.secrets.get("private_key"):
            params.update(pkey=_load_pkey(self.secrets["private_key"]), use_keys=True, allow_agent=False, look_for_keys=False)
        else:
            params.update(password=self.secrets.get("password", ""), allow_agent=False, look_for_keys=False)
        if self.secrets.get("enable"):
            params["secret"] = self.secrets["enable"]
        try:
            self._conn = ConnectHandler(**params)
            if self.secrets.get("enable") and "cisco" in self.device_type:
                self._conn.enable()
        except NetmikoAuthenticationException as e:
            raise TransportError("Authentication failed (check the credential profile).", "auth_failed") from e
        except NetmikoTimeoutException as e:
            raise TransportError(f"Timed out connecting to {self.host}.", "timeout") from e
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"SSH session could not be opened: {e}", "connection_failed") from e

    def disconnect(self) -> None:
        if self._conn:
            try:
                self._conn.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    def _c(self):
        if not self._conn:
            raise TransportError("Not connected", "not_connected")
        return self._conn

    def run(self, command: str) -> str:
        try:
            return self._c().send_command(command, read_timeout=60, strip_prompt=True, strip_command=True)
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"Command failed or connection lost: {e}", "connection_lost") from e

    def run_config(self, commands: list[str]) -> str:
        c = self._c()
        try:
            if "mikrotik" in self.device_type or "fortinet" in self.device_type:
                # no separate config mode: send lines one by one and collect output
                out = []
                for ln in commands:
                    out.append(c.send_command_timing(ln, read_timeout=30, strip_prompt=True, strip_command=True))
                return "\n".join(out)
            return c.send_config_set(commands, read_timeout=60, cmd_verify=False)
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"Connection lost while applying configuration: {e}", "connection_lost") from e

    def run_interactive(self, command: str) -> str:
        c = self._c()
        out = c.send_command_timing(command, read_timeout=15)
        for _ in range(3):
            if re.search(r"\[yes/no\]", out, re.I):
                out += c.send_command_timing("no", read_timeout=10)
            elif re.search(r"\[confirm\]", out, re.I):
                out += c.send_command_timing("\n", read_timeout=10)
            elif re.search(r"\(y/n\)|\[y/n\]|yes, \[y/N\]|y/N", out, re.I):
                out += c.send_command_timing("y", read_timeout=10)
            else:
                break
        return out
