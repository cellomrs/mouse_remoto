"""Servidor do Mouse Remoto: serve o PWA e recebe eventos via WebSocket.

Uso:
    python server/main.py [--https] [--port 8000] [--host 0.0.0.0] [--new-token]
    pythonw server/main.py --tray [--https]     # em segundo plano, com ícone na bandeja
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import secrets
import socket
from pathlib import Path
from urllib.parse import urlparse

from input_controller import enable_dpi_awareness

# Precisa vir antes de criar o Controller do pynput.
enable_dpi_awareness()

import uvicorn  # noqa: E402
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from pynput.keyboard import Controller as KeyboardController  # noqa: E402
from pynput.mouse import Controller  # noqa: E402

import monitor_manager as mm  # noqa: E402
from input_controller import InputSession  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
TOKEN_FILE = Path(__file__).resolve().parent / "token.txt"

MAX_MOVE = 2000.0   # px por mensagem; descarta valores absurdos
MAX_SCROLL = 50.0   # entalhes por mensagem
MONITOR_POLL_S = 3  # intervalo para detectar TV ligada/desligada

mouse = Controller()
keyboard = KeyboardController()
app = FastAPI()
TOKEN = ""
ACTIVE: set[WebSocket] = set()             # conexões autenticadas
SERVER_LOOP: asyncio.AbstractEventLoop | None = None


def load_token(regenerate: bool) -> str:
    if not regenerate and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(12)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def rotate_token() -> str:
    """Gera um novo token e derruba os celulares conectados com o token antigo.

    Pode ser chamado de outra thread (ex.: menu da bandeja).
    """
    global TOKEN
    TOKEN = load_token(regenerate=True)

    async def kick_all() -> None:
        for ws in list(ACTIVE):
            try:
                await ws.close(code=4401)
            except Exception:
                pass

    if SERVER_LOOP is not None and SERVER_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(kick_all(), SERVER_LOOP)
    return TOKEN


def lan_ip() -> str:
    """IP da interface usada para sair para a rede (nenhum pacote é enviado)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _num(value, limit: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(n):
        return 0.0
    return max(-limit, min(limit, n))


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # clientes não-navegador
    return urlparse(origin).netloc == ws.headers.get("host")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    token = ws.query_params.get("token", "")
    if not _origin_ok(ws):
        await ws.close(code=1008)  # rejeita no handshake (HTTP 403)
        return

    await ws.accept()
    if not secrets.compare_digest(token, TOKEN):
        # Aceita e fecha com 4401 para o navegador saber que é token inválido
        # (rejeitar no handshake chega no JS só como 1006 genérico).
        await ws.close(code=4401)
        return

    global SERVER_LOOP
    SERVER_LOOP = asyncio.get_running_loop()
    ACTIVE.add(ws)
    session = InputSession(mouse, keyboard)
    client = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    print(f"[+] conectado: {client}")

    monitors = mm.list_monitors()

    async def send(obj: dict) -> None:
        await ws.send_text(json.dumps(obj))

    async def send_monitors() -> None:
        await send({"type": "monitors", "list": mm.to_payload(monitors)})

    async def send_lock_state() -> None:
        await send({
            "type": "lock_state",
            "enabled": session.lock is not None,
            "monitor": session.lock.id if session.lock else None,
        })

    def refresh_monitors() -> bool:
        """Relê os monitores; ajusta/solta a trava. Retorna True se algo mudou."""
        nonlocal monitors
        new = mm.list_monitors()
        if new == monitors:
            return False
        monitors = new
        if session.lock is not None:
            session.lock = mm.find(monitors, session.lock.id)  # None se a TV sumiu
        return True

    async def watch_monitors() -> None:
        while True:
            await asyncio.sleep(MONITOR_POLL_S)
            had_lock = session.lock is not None
            if refresh_monitors():
                await send_monitors()
                if had_lock:
                    await send_lock_state()

    await send({"type": "hello"})
    await send_monitors()
    await send_lock_state()
    watcher = asyncio.create_task(watch_monitors())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            kind = msg.get("type")
            if kind == "move":
                session.move(_num(msg.get("dx"), MAX_MOVE), _num(msg.get("dy"), MAX_MOVE))
            elif kind == "scroll":
                session.scroll(_num(msg.get("dx"), MAX_SCROLL), _num(msg.get("dy"), MAX_SCROLL))
            elif kind == "click":
                session.click(str(msg.get("button", "left")), str(msg.get("action", "tap")))
            elif kind == "ping":
                await send({"type": "pong", "t": msg.get("t")})
            elif kind == "media":
                session.media(str(msg.get("command", "")))
            elif kind == "key":
                session.key(str(msg.get("key", "")))
            elif kind == "text":
                value = msg.get("value")
                if isinstance(value, str):
                    session.text(value)
            elif kind == "get_monitors":
                refresh_monitors()
                await send_monitors()
            elif kind == "focus_monitor":
                if refresh_monitors():
                    await send_monitors()
                target = mm.find(monitors, msg.get("monitor"))
                if target is not None:
                    session.teleport(*target.center)
                    if session.lock is not None and session.lock.id != target.id:
                        # Teleportar para outra tela leva a trava junto.
                        session.lock = target
                        await send_lock_state()
            elif kind == "lock_monitor":
                if refresh_monitors():
                    await send_monitors()
                if msg.get("enabled"):
                    target = mm.find(monitors, msg.get("monitor")) or mm.monitor_at(
                        monitors, *session.position
                    )
                    session.lock = target
                    if target is not None and not target.contains(*session.position):
                        session.teleport(*target.center)
                else:
                    session.lock = None
                await send_lock_state()
    except WebSocketDisconnect:
        pass
    finally:
        ACTIVE.discard(ws)
        watcher.cancel()
        session.release_all()
        print(f"[-] desconectado: {client}")


@app.get("/ca.crt")
async def ca_cert() -> Response:
    """Certificado público da CA local, para instalar no celular (modo --https)."""
    import certs

    if not certs.CA_CRT.exists():
        return Response("Servidor não está em modo --https.", status_code=404)
    return FileResponse(
        certs.CA_CRT,
        media_type="application/x-x509-ca-cert",
        filename="mouse-remoto-ca.crt",
    )


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def print_banner(url: str) -> None:
    print("\n  Mouse Remoto rodando")
    print(f"  Abra no celular: {url}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:  # QR é só conveniência
        print(f"  (não foi possível gerar o QR Code: {exc})")
    print("  Se o celular não conectar, libere o Python no Firewall do Windows (Rede Privada).\n")


class Settings:
    """Configuração resolvida a partir dos argumentos de linha de comando."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.host: str = args.host
        self.port: int = args.port
        self.https: bool = args.https
        self.scheme = "https" if args.https else "http"
        self.ssl: dict = {}
        self.ca_created = False

    def prepare(self) -> None:
        global TOKEN
        TOKEN = load_token(self.args.new_token)
        if self.https:
            import certs

            certfile, keyfile, self.ca_created = certs.ensure_certs([lan_ip()])
            self.ssl = {"ssl_certfile": str(certfile), "ssl_keyfile": str(keyfile)}

    @property
    def url(self) -> str:
        # Relido a cada chamada: o IP pode mudar e o token pode ser trocado.
        return f"{self.scheme}://{lan_ip()}:{self.port}/?t={TOKEN}"

    def uvicorn_config(self) -> uvicorn.Config:
        return uvicorn.Config(
            app, host=self.host, port=self.port, log_level="warning",
            ws_ping_interval=10, **self.ssl,
        )

    def startup_args(self) -> list[str]:
        """Argumentos para repetir esta configuração ao iniciar com o Windows."""
        out = ["--tray"]
        if self.https:
            out.append("--https")
        if self.port != 8000:
            out += ["--port", str(self.port)]
        if self.host != "0.0.0.0":
            out += ["--host", self.host]
        return out


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Servidor do Mouse Remoto")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--new-token", action="store_true", help="gera um novo token de acesso")
    parser.add_argument(
        "--https",
        action="store_true",
        help="serve via HTTPS com CA local (necessário para instalar o app e manter a tela ligada)",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="roda em segundo plano com ícone na bandeja do sistema (sem terminal)",
    )
    return parser.parse_args(argv)


def main() -> None:
    settings = Settings(parse_args())

    if settings.args.tray:
        import tray

        tray.run(settings, rotate_token=rotate_token, port_in_use=port_in_use)
        return

    if port_in_use(settings.host, settings.port):
        print(f"\n  A porta {settings.port} já está em uso — o Mouse Remoto já está rodando"
              " (talvez na bandeja)? Use --port para escolher outra.\n")
        raise SystemExit(1)

    settings.prepare()
    if settings.https:
        import certs

        if settings.ca_created:
            print("\n  Certificado da CA criado em", certs.CA_CRT)
        print(
            "\n  HTTPS: instale o certificado da CA no celular UMA vez. Abra o endereço abaixo,"
            "\n  aceite o aviso de segurança e toque em 'Baixar certificado' (instruções na tela)."
        )

    print_banner(settings.url)
    uvicorn.Server(settings.uvicorn_config()).run()


if __name__ == "__main__":
    main()
