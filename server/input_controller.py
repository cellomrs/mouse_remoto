"""Abstração sobre o pynput: movimento relativo, cliques e rolagem.

Cada conexão WebSocket recebe o seu próprio InputSession, que:
  * acumula frações de pixel / de entalhe de scroll entre eventos;
  * lembra quais botões estão pressionados, para soltá-los se a conexão cair.
"""

from __future__ import annotations

import math
import sys

from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key
from pynput.mouse import Button, Controller

_BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}

# Teclas de mídia do sistema (funcionam com o player em foco ou em segundo plano).
_MEDIA = {
    "play_pause": Key.media_play_pause,
    "next": Key.media_next,
    "prev": Key.media_previous,
    "volume_up": Key.media_volume_up,
    "volume_down": Key.media_volume_down,
    "mute": Key.media_volume_mute,
}

# Teclas comuns (players web: Espaço = play/pause, F = tela cheia, setas = avançar).
_KEYS = {
    "space": Key.space,
    "enter": Key.enter,
    "backspace": Key.backspace,
    "esc": Key.esc,
    "tab": Key.tab,
    "left": Key.left,
    "right": Key.right,
    "up": Key.up,
    "down": Key.down,
    "f": "f",
}

MAX_TEXT = 500

# Menor passo de scroll que o Windows entende (1/120 de entalhe).
_SCROLL_STEP = 1 / 120


def enable_dpi_awareness() -> None:
    """Faz o processo enxergar pixels físicos em todos os monitores (Windows).

    Sem isso, com monitores em escalas diferentes (ex.: 100% e 150%), as
    coordenadas absolutas do cursor ficam "virtualizadas" e o teleporte/trava
    de tela da Fase 2 erraria a posição. Precisa rodar antes de qualquer
    chamada que leia posição de cursor ou de monitores.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _take_whole(value: float, step: float = 1.0) -> tuple[float, float]:
    """Separa `value` em (parte enviável em múltiplos de `step`, resto)."""
    whole = math.trunc(value / step) * step
    return whole, value - whole


class InputSession:
    def __init__(self, mouse: Controller, keyboard: KeyboardController) -> None:
        self._mouse = mouse
        self._kb = keyboard
        self._rem_x = 0.0
        self._rem_y = 0.0
        self._scroll_rem_x = 0.0
        self._scroll_rem_y = 0.0
        self._pressed: set[Button] = set()
        # Monitor ao qual o cursor está preso (qualquer objeto com .clamp(x, y)),
        # ou None para movimento livre.
        self.lock = None

    def move(self, dx: float, dy: float) -> None:
        sx, self._rem_x = _take_whole(self._rem_x + dx)
        sy, self._rem_y = _take_whole(self._rem_y + dy)
        if not (sx or sy):
            return
        if self.lock is None:
            self._mouse.move(int(sx), int(sy))
        else:
            x, y = self._mouse.position
            self._mouse.position = self.lock.clamp(x + sx, y + sy)

    @property
    def position(self) -> tuple[int, int]:
        return self._mouse.position

    def teleport(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)
        self._rem_x = self._rem_y = 0.0

    def scroll(self, dx: float, dy: float) -> None:
        """dx/dy em entalhes de roda (podem ser frações). dy > 0 rola para cima."""
        sx, self._scroll_rem_x = _take_whole(self._scroll_rem_x + dx, _SCROLL_STEP)
        sy, self._scroll_rem_y = _take_whole(self._scroll_rem_y + dy, _SCROLL_STEP)
        if sx or sy:
            self._mouse.scroll(sx, sy)

    def click(self, button: str, action: str) -> None:
        btn = _BUTTONS.get(button)
        if btn is None:
            return
        if action == "tap":
            self._mouse.click(btn)
        elif action == "down":
            if btn not in self._pressed:
                self._mouse.press(btn)
                self._pressed.add(btn)
        elif action == "up":
            if btn in self._pressed:
                self._mouse.release(btn)
                self._pressed.discard(btn)

    def media(self, command: str) -> None:
        key = _MEDIA.get(command)
        if key is not None:
            self._kb.tap(key)

    def key(self, name: str) -> None:
        key = _KEYS.get(name)
        if key is not None:
            self._kb.tap(key)

    def text(self, value: str) -> None:
        if value:
            self._kb.type(value[:MAX_TEXT])

    def release_all(self) -> None:
        """Chamado ao desconectar: nunca deixar um botão preso no PC."""
        for btn in list(self._pressed):
            try:
                self._mouse.release(btn)
            except Exception:
                pass
        self._pressed.clear()
