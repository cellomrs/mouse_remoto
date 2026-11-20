"""CA local + certificado do servidor para servir o PWA via HTTPS na rede local.

Por que: Service Worker (instalar como app) e Wake Lock (tela sempre ligada)
só funcionam em contexto seguro. Um IP de LAN em HTTP não é contexto seguro.

Fluxo:
  * A CA é criada uma única vez (validade de 10 anos) e instalada no celular.
  * O certificado do servidor é assinado por essa CA e reemitido sozinho quando
    o IP do PC muda ou quando está perto de vencer — sem reinstalar nada no celular.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_DIR = Path(__file__).resolve().parent / "certs"
CA_KEY = CERT_DIR / "ca.key"
CA_CRT = CERT_DIR / "ca.crt"
SRV_KEY = CERT_DIR / "server.key"
SRV_CRT = CERT_DIR / "server.crt"

SERVER_DAYS = 396      # Safari/Chrome recusam certificados de servidor com mais de 398 dias no total
RENEW_BEFORE_DAYS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_key(path: Path, key) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _load_key(path: Path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _ensure_ca():
    if CA_KEY.exists() and CA_CRT.exists():
        return _load_key(CA_KEY), x509.load_pem_x509_certificate(CA_CRT.read_bytes())

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Mouse Remoto CA ({socket.gethostname()})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mouse Remoto (local)"),
    ])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        # Segurança: mesmo se ca.key vazar, esta CA só consegue emitir certificados
        # para IPs de rede local e para este PC — nunca para sites da internet.
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[
                    x509.DNSName("localhost"),
                    x509.DNSName(socket.gethostname()),
                    x509.IPAddress(ipaddress.ip_network("10.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("172.16.0.0/12")),
                    x509.IPAddress(ipaddress.ip_network("192.168.0.0/16")),
                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                ],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_key(CA_KEY, key)
    CA_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _server_cert_ok(ips: list[str]) -> bool:
    if not (SRV_KEY.exists() and SRV_CRT.exists()):
        return False
    try:
        cert = x509.load_pem_x509_certificate(SRV_CRT.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    except Exception:
        return False
    if not set(ips) <= have:
        return False
    return cert.not_valid_after_utc - _now() > dt.timedelta(days=RENEW_BEFORE_DAYS)


def ensure_certs(ips: list[str]) -> tuple[Path, Path, bool]:
    """Garante CA + certificado do servidor válido para `ips`.

    Retorna (certfile, keyfile, ca_criada_agora).
    """
    CERT_DIR.mkdir(exist_ok=True)
    ca_is_new = not CA_CRT.exists()
    ca_key, ca_cert = _ensure_ca()

    ips = sorted(set(ips) | {"127.0.0.1"})
    if not _server_cert_ok(ips):
        key = ec.generate_private_key(ec.SECP256R1())
        now = _now()
        host = socket.gethostname()
        san = [x509.DNSName("localhost"), x509.DNSName(host)]
        san += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write_key(SRV_KEY, key)
        SRV_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return SRV_CRT, SRV_KEY, ca_is_new
