"""PWAs instalados no PC (Chrome/Edge): descobrir, abrir e posicionar na TV.

Descoberta: atalhos .lnk do menu Iniciar cujos argumentos têm `--app-id=` (é assim
que Chrome e Edge instalam apps web no Windows). O .lnk é lido em Python puro
(formato MS-SHLLINK), sem depender de pywin32.

Segurança: o celular só envia o app-id; só são abertos atalhos da lista descoberta
aqui. Nenhum caminho ou comando vem do celular.
"""

from __future__ import annotations

import base64
import io
import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_APP_ID = re.compile(r"--app-id=([a-z]{32})")
_BROWSERS = {"chrome.exe", "msedge.exe"}
ICON_SIZE = 96


@dataclass(frozen=True)
class App:
    id: str
    name: str
    lnk: Path
    icon_path: str

    def payload(self) -> dict:
        return {"id": self.id, "name": self.name, "icon": _icon_data_url(self.icon_path)}


# ---------------------------------------------------------------- .lnk

def read_lnk(path: Path) -> dict:
    """Lê argumentos e ícone de um atalho do Windows (MS-SHLLINK)."""
    data = path.read_bytes()
    if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        raise ValueError("não é um .lnk")
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 76
    if flags & 0x01:  # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if flags & 0x02:  # HasLinkInfo
        pos += struct.unpack_from("<I", data, pos)[0]
    unicode = bool(flags & 0x80)

    out = {}
    # StringData, nesta ordem, cada um presente conforme a sua flag.
    for bit, key in ((0x04, "name"), (0x08, "relative_path"), (0x10, "working_dir"),
                     (0x20, "arguments"), (0x40, "icon_location")):
        if not flags & bit:
            continue
        count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        size = count * 2 if unicode else count
        raw = data[pos:pos + size]
        pos += size
        out[key] = raw.decode("utf-16-le" if unicode else "mbcs" if sys.platform == "win32" else "latin-1",
                              errors="replace")
    return out


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("APPDATA", "ProgramData"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def list_pwas() -> list[App]:
    if sys.platform != "win32":
        return []
    found: dict[str, App] = {}
    for root in _start_menu_dirs():
        for lnk in root.rglob("*.lnk"):
            try:
                info = read_lnk(lnk)
            except (OSError, ValueError, struct.error):
                continue
            m = _APP_ID.search(info.get("arguments", ""))
            if not m or m.group(1) in found:
                continue
            name = re.sub(r"\s+", " ", lnk.stem).strip()
            icon = os.path.expandvars(info.get("icon_location", ""))
            found[m.group(1)] = App(id=m.group(1), name=name, lnk=lnk, icon_path=icon)
    return sorted(found.values(), key=lambda a: a.name.casefold())


# ---------------------------------------------------------------- ícones

_icon_cache: dict[tuple[str, float], str | None] = {}


def _icon_data_url(path: str) -> str | None:
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _icon_cache:
        try:
            from PIL import Image

            with Image.open(path) as im:  # .ico: abre o maior tamanho disponível
                img = im.convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            _icon_cache[key] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            _icon_cache[key] = None
    return _icon_cache[key]


# ---------------------------------------------------------------- janelas (Windows)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    def _process_name(hwnd) -> str:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name.lower()
            return ""
        finally:
            _kernel32.CloseHandle(handle)

    def _title(hwnd) -> str:
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _is_app_window(hwnd) -> bool:
        return bool(
            hwnd
            and _user32.IsWindowVisible(hwnd)
            and not _user32.GetWindow(hwnd, 4)  # GW_OWNER: sem dono = janela principal
            and _user32.GetWindowTextLengthW(hwnd) > 0
            and _process_name(hwnd) in _BROWSERS
        )

    def _browser_windows() -> set[int]:
        result: set[int] = set()

        def cb(hwnd, _):
            if _is_app_window(hwnd):
                result.add(hwnd)
            return True

        _user32.EnumWindows(_WNDENUMPROC(cb), 0)
        return result

    def _wait_for_window(before: set[int], fg_before, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.2)
            fg = _user32.GetForegroundWindow()
            new = _browser_windows() - before
            if new:
                return fg if fg in new else next(iter(new))
            if fg and fg != fg_before and _is_app_window(fg):
                return fg  # app já estava aberto: o Chrome só trouxe a janela à frente
        # Sem palpites (ex.: pelo título): melhor não mover nada do que mover
        # uma janela que o usuário já tinha aberta.
        return None

    def _place(hwnd, rect: tuple[int, int, int, int]) -> None:
        x, y, w, h = rect
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE (sair de maximizado para poder mover)
        _user32.SetWindowPos(hwnd, None, x + 40, y + 40, max(w - 80, 200), max(h - 80, 200),
                             0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE
        _user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE no monitor onde a janela está agora
        _user32.SetForegroundWindow(hwnd)


# O Chrome leva ~5 s (às vezes >8 s) para mostrar a janela de um PWA.
LAUNCH_TIMEOUT_S = 20.0


def launch(app: App, rect: tuple[int, int, int, int] | None,
           timeout: float = LAUNCH_TIMEOUT_S) -> dict:
    """Abre o app e, se `rect` (x, y, w, h) for dado, maximiza a janela nesse monitor.

    Bloqueante (espera a janela aparecer): chamar via asyncio.to_thread.
    """
    if sys.platform != "win32":
        return {"ok": False, "placed": False}
    before = _browser_windows()
    fg_before = _user32.GetForegroundWindow()
    try:
        os.startfile(str(app.lnk))
    except OSError:
        return {"ok": False, "placed": False}
    if rect is None:
        return {"ok": True, "placed": False}
    hwnd = _wait_for_window(before, fg_before, timeout)
    if not hwnd:
        return {"ok": True, "placed": False}
    time.sleep(0.3)  # deixa o Chrome terminar de criar a janela
    _place(hwnd, rect)
    return {"ok": True, "placed": True, "hwnd": int(hwnd)}
