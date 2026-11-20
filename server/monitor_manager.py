"""Detecção de monitores (screeninfo), teleporte e trava de tela.

Monitores são identificados pelo nome do sistema (ex.: "\\\\.\\DISPLAY2"), não
pelo índice: a ordem muda quando a TV é desligada/religada no HDMI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from screeninfo import get_monitors


@dataclass(frozen=True)
class Monitor:
    id: str
    x: int
    y: int
    w: int
    h: int
    primary: bool

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def clamp(self, px: float, py: float) -> tuple[int, int]:
        cx = min(max(int(px), self.x), self.x + self.w - 1)
        cy = min(max(int(py), self.y), self.y + self.h - 1)
        return cx, cy


def list_monitors() -> list[Monitor]:
    try:
        raw = get_monitors()
    except Exception:
        return []
    mons = [
        Monitor(
            id=m.name or f"monitor{i}",
            x=m.x,
            y=m.y,
            w=m.width,
            h=m.height,
            primary=bool(m.is_primary),
        )
        for i, m in enumerate(raw)
    ]
    # Ordem estável e intuitiva: da esquerda para a direita, de cima para baixo.
    mons.sort(key=lambda m: (m.x, m.y))
    return mons


def to_payload(monitors: list[Monitor]) -> list[dict]:
    return [asdict(m) for m in monitors]


def find(monitors: list[Monitor], monitor_id: str | None) -> Monitor | None:
    return next((m for m in monitors if m.id == monitor_id), None)


def monitor_at(monitors: list[Monitor], px: float, py: float) -> Monitor | None:
    return next((m for m in monitors if m.contains(px, py)), None)
