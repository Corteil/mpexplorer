"""What belongs to one connected board (GUI-free). Side-by-side panes will hold one each: docs/SIDE_BY_SIDE_PLAN.md."""
from dataclasses import dataclass

from .backends import DeviceBackend
from .console import ConsoleSession


@dataclass
class Connection:
    port: str
    board: str              # settings key: its chip id, or its USB id if the firmware cannot give one
    device: DeviceBackend
    session: ConsoleSession
    badge: object = None    # BadgeScreen while a board with a screen is connected

    @classmethod
    def open_on(cls, device, port, board):
        return cls(port, board, device, ConsoleSession(device.repl))
