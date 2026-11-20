"""Modo bandeja: servidor em segundo plano + ícone na área de notificação.

Iniciado por `pythonw server/main.py --tray` (sem janela de terminal).
Este módulo NÃO importa `main` (que roda como __main__); recebe o que precisa
por parâmetro para não criar uma segunda cópia do app/token.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import pystray
import qrcode
import uvicorn
from PIL import Image

import autostart

SERVER_DIR = Path(__file__).resolve().parent
LOG_FILE = SERVER_DIR / "mouse_remoto.log"
ICON_FILE = SERVER_DIR.parent / "web" / "icons" / "icon-192.png"
TITLE = "Mouse Remoto"

MB_OK_INFO = 0x40
MB_OK_WARN = 0x30
MB_YESNO_QUESTION = 0x04 | 0x20
IDYES = 6
MB_TOPMOST = 0x40000


def _redirect_output() -> None:
    """Com pythonw não há terminal: manda prints e erros para um arquivo de log."""
    if sys.stdout is None or sys.stderr is None or Path(sys.executable).stem.lower() == "pythonw":
        log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} iniciando ===")


def _msgbox(text: str, flags: int = MB_OK_INFO) -> int:
    if sys.platform != "win32":
        print(text)
        return IDYES
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_TOPMOST)


def _copy_to_clipboard(text: str) -> None:
    subprocess.run("clip", input=text.encode("ascii", "ignore"), creationflags=0x08000000, check=False)


class QrWindow:
    """Janela com o QR Code. Roda o Tk numa thread própria; reabrir só a traz à frente."""

    def __init__(self, get_url: Callable[[], str], https: bool) -> None:
        self._get_url = get_url
        self._https = https
        self._thread: threading.Thread | None = None
        self._raise = False
        self._close = False

    def open(self) -> None:
        if self._thread and self._thread.is_alive():
            self._raise = True
            return
        self._close = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._close = True

    def _run(self) -> None:
        import tkinter as tk

        from PIL import ImageTk

        root = tk.Tk()
        root.title(TITLE)
        root.configure(bg="#111318", padx=20, pady=16)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        try:
            root.iconphoto(True, ImageTk.PhotoImage(Image.open(ICON_FILE)))
        except Exception:
            pass

        tk.Label(root, text="Escaneie com a câmera do celular", fg="#e6e8ee", bg="#111318",
                 font=("Segoe UI", 12, "bold")).pack()
        qr_label = tk.Label(root, bg="#ffffff", bd=0)
        qr_label.pack(pady=12)
        url_label = tk.Label(root, fg="#e6e8ee", bg="#1b1e25", font=("Consolas", 10),
                             padx=10, pady=6, wraplength=520)
        url_label.pack()
        hint = "O endereço contém a senha de acesso — não compartilhe."
        if self._https:
            hint += "\nPrimeira vez? No celular, toque em “Baixar certificado” e siga as instruções."
        tk.Label(root, text=hint, fg="#8a90a0", bg="#111318", font=("Segoe UI", 9),
                 justify="center").pack(pady=(10, 0))

        state = {"url": None, "img": None}

        def render() -> None:
            url = self._get_url()
            if url == state["url"]:
                return
            state["url"] = url
            img = qrcode.make(url, box_size=8, border=2).get_image().convert("RGB")
            state["img"] = ImageTk.PhotoImage(img)
            qr_label.configure(image=state["img"])
            url_label.configure(text=url)

        def poll() -> None:
            if self._close:
                root.destroy()
                return
            if self._raise:
                self._raise = False
                root.deiconify()
                root.lift()
                root.focus_force()
            render()  # atualiza se o token ou o IP mudarem
            root.after(500, poll)

        render()
        root.after(500, poll)
        root.mainloop()

        # Objetos do Tk precisam ser liberados nesta mesma thread.
        import gc

        state.clear()
        del render, poll, qr_label, url_label, root
        gc.collect()


def run(
    settings,
    rotate_token: Callable[[], str],
    port_in_use: Callable[[str, int], bool],
) -> None:
    _redirect_output()

    if port_in_use(settings.host, settings.port):
        _msgbox(
            f"A porta {settings.port} já está em uso.\n\n"
            "O Mouse Remoto provavelmente já está rodando — procure o ícone na bandeja.",
            MB_OK_WARN,
        )
        return

    try:
        settings.prepare()
        server = uvicorn.Server(settings.uvicorn_config())
    except Exception as exc:
        _msgbox(f"Não foi possível iniciar o servidor:\n\n{exc}", MB_OK_WARN)
        raise

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    for _ in range(100):  # até 10 s
        if server.started or not server_thread.is_alive():
            break
        time.sleep(0.1)
    if not server.started:
        _msgbox(f"O servidor não iniciou. Veja o log:\n{LOG_FILE}", MB_OK_WARN)
        return
    print("servidor no ar:", settings.url.split("?")[0])

    qr = QrWindow(lambda: settings.url, settings.https)

    def show_qr(icon, item) -> None:
        qr.open()

    def copy_url(icon, item) -> None:
        _copy_to_clipboard(settings.url)
        icon.notify("Endereço copiado.", TITLE)

    def toggle_autostart(icon, item) -> None:
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable(settings.startup_args())

    def new_token(icon, item) -> None:
        def ask() -> None:
            answer = _msgbox(
                "Gerar um novo token de acesso?\n\n"
                "Os celulares conectados serão desconectados e precisarão escanear o novo QR Code.",
                MB_YESNO_QUESTION,
            )
            if answer == IDYES:
                rotate_token()
                print("token trocado pelo menu da bandeja")
                qr.open()

        threading.Thread(target=ask, daemon=True).start()

    def quit_app(icon, item) -> None:
        qr.close()
        server.should_exit = True
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Mostrar QR Code", show_qr, default=True),
        pystray.MenuItem("Copiar endereço", copy_url),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Iniciar com o Windows",
            toggle_autostart,
            checked=lambda item: autostart.is_enabled(),
            visible=autostart.supported(),
        ),
        pystray.MenuItem("Gerar novo token…", new_token),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", quit_app),
    )

    icon = pystray.Icon("mouse_remoto", Image.open(ICON_FILE), TITLE, menu)

    def setup(icon) -> None:
        icon.visible = True
        icon.notify(
            f"Rodando em {settings.url.split('?')[0]}\nClique no ícone para ver o QR Code.", TITLE
        )

    icon.run(setup=setup)

    server.should_exit = True
    server_thread.join(timeout=5)
    print("encerrado")
