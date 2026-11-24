from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def run(self, command: str) -> str:
        """Run one read-only command and return its output."""
    def run_config(self, commands: list[str]) -> str:
        """Send configuration commands (vendor adapter already built them)."""
    def run_interactive(self, command: str) -> str:
        """Run a command that asks for confirmation (reboot)."""
