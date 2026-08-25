"""Adresse ermitteln, unter der das Dashboard im WLAN erreichbar ist.

Beim Start zeigt ``run.sh`` darüber die Netzwerk-Adresse und einen QR-Code -
einmal mit der Handykamera scannen, statt eine IP abzutippen.

Direkt aufrufbar, falls der Code im Terminal weggescrollt ist:

    python3 -m vocabtrainer.netinfo
"""

from __future__ import annotations

import argparse
import io
import socket

DEFAULT_PORT = 8501

# Der QR-Code besteht aus Halbblock-Zeichen. Ob er "richtig herum" ist, haengt
# an der Hintergrundfarbe des Terminals: dunkle Terminals brauchen invertiert,
# helle nicht. Dunkel ist heute die Voreinstellung der meisten Terminals.
DEFAULT_INVERT = True


def lan_ip() -> str | None:
    """IP-Adresse dieses Rechners im lokalen Netz, oder ``None``.

    Der UDP-"Verbindungsaufbau" schickt kein einziges Paket - er dient nur
    dazu, das Betriebssystem nach der Route und damit nach der passenden
    Quelladresse zu fragen. Das funktioniert auch ohne Internetzugang.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, absichtlich nicht geroutet
        address = sock.getsockname()[0]
    except OSError:
        address = ""
    finally:
        sock.close()

    if not address or address.startswith("127."):
        try:  # Rueckfallebene, z.B. wenn keine Default-Route gesetzt ist
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    if not address or address.startswith("127."):
        return None
    return address


def mdns_hostname() -> str | None:
    """Name des Rechners im lokalen Netz, z.B. ``macbook.local``."""
    try:
        name = socket.gethostname().strip()
    except OSError:
        return None
    name = name.split(".")[0]  # ein FQDN kommt hier ohne Domain wieder raus
    if not name or name.lower() == "localhost":
        return None
    return f"{name}.local"


def urls(port: int = DEFAULT_PORT) -> list[tuple[str, str]]:
    """Alle Adressen, unter denen das Dashboard erreichbar ist."""
    found: list[tuple[str, str]] = [("Auf diesem Rechner", f"http://localhost:{port}")]
    ip = lan_ip()
    if ip:
        found.append(("Im WLAN (fürs Handy)", f"http://{ip}:{port}"))
    host = mdns_hostname()
    if host:
        found.append(("Oder per Name", f"http://{host}:{port}"))
    return found


def phone_url(port: int = DEFAULT_PORT) -> str | None:
    """Die Adresse, die auf den QR-Code kommt (die IP ist am zuverlässigsten)."""
    ip = lan_ip()
    return f"http://{ip}:{port}" if ip else None


def qr_lines(url: str, invert: bool = DEFAULT_INVERT) -> list[str] | None:
    """QR-Code als Terminalzeilen, oder ``None`` wenn ``qrcode`` fehlt."""
    try:
        import qrcode
    except ImportError:
        return None
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=4,  # der Ruhebereich gehoert zum Code, sonst scannt er schlecht
    )
    code.add_data(url)
    code.make(fit=True)
    buffer = io.StringIO()
    code.print_ascii(out=buffer, invert=invert)
    return buffer.getvalue().rstrip("\n").split("\n")


def banner(port: int = DEFAULT_PORT, invert: bool = DEFAULT_INVERT) -> str:
    lines = ["", "  Vokabeltrainer läuft:", ""]
    width = max(len(label) for label, _ in urls(port))
    for label, url in urls(port):
        lines.append(f"  {label.ljust(width)}   {url}")

    url = phone_url(port)
    if url is None:
        lines += ["", "  (Keine WLAN-Adresse gefunden – ist der Rechner im Netz?)", ""]
        return "\n".join(lines)

    code = qr_lines(url, invert)
    if code is None:
        lines += ["", "  (Für den QR-Code fehlt das Paket 'qrcode' – ./run.sh installiert es.)", ""]
        return "\n".join(lines)

    lines += ["", "  Mit der Handykamera scannen:", ""]
    lines += [f"  {line}" for line in code]
    lines += ["", "  Falls die Kamera nichts erkennt, ist der Code für dein Terminal", 
              "  invertiert – dann einmal mit FRANZ_QR_INVERT=0 ./run.sh starten.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Netzwerk-Adresse und QR-Code anzeigen.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-invert",
        action="store_true",
        help="QR-Code für helle Terminal-Hintergründe",
    )
    args = parser.parse_args(argv)
    print(banner(args.port, invert=not args.no_invert))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
