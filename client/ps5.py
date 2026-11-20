"""Talking to the presence payload running on the PS5."""
import json
import logging
import socket
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# Classes
@dataclass
class GameState:
    title_id: str
    elapsed: int


class ConsoleUnreachable(Exception): pass


# Functions
def parse_game(message: list[str]) -> GameState | None:
    if not message:
        raise ConsoleUnreachable
    if message[0] == "IDLE":
        return None
    elif message[0] == "GAME":
        return GameState(message[1], int(message[2]))
    else:
        raise ConsoleUnreachable


def fetch_state(host: str, port: int = 8000) -> GameState | None:
    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            message = sock.makefile("r").readline().split()
            state = parse_game(message)
            log.debug("polled %s:%s -> %s", host, port, state)
            return state
    except OSError as e:
        raise ConsoleUnreachable from e


def fetch_param(host: str, port: int = 8000) -> dict | None:
    """Ask the payload for the running game's param.json.

    Returns None when the console sent no file, which is the normal case for
    PS4 titles.
    """
    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.sendall(b"PARAM\n")
            data = sock.makefile("r", encoding="utf-8").read()
            line, _, rest = data.partition("\n")
            log.debug("PARAM reply: state line %r, %d bytes of JSON", line, len(rest))
            try:
                return json.loads(rest)
            except json.JSONDecodeError:
                return None

    except OSError as e:
        raise ConsoleUnreachable from e


@dataclass
class DiscInfo:
    kind: str                 # "BD", "DVD" or "NONE"
    name: str | None = None   # from the Blu-ray's own metadata, if it has any
    uhd: bool = False            # a 4K Ultra HD Blu-ray
    label: str | None = None     # the disc's volume name, e.g. "THE_BACKROOMS"
    raw: str = ""                # the payload's whole answer, for the log


def fetch_disc(host: str, port: int = 8000, debug: bool = False) -> DiscInfo | None:
    """Ask the payload what movie disc is in the drive.

    None when the payload is older and doesn't know the command.
    """
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"DISCDEBUG\n" if debug else b"DISC\n")
            data = sock.makefile("r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        raise ConsoleUnreachable from e
    info = None
    for line in data.splitlines()[1:]:  # the first line is the usual state line
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "TYPE":
            info = DiscInfo(value or "NONE")
        elif info and key == "NAME" and value:
            info.name = value
        elif info and key == "FORMAT":
            info.uhd = value == "UHD"
        elif info and key == "LABEL" and value:
            info.label = value
    if info:
        info.raw = data
    log.debug("DISC reply: %r -> %s", data, info)
    return info


def fetch_raw(host: str, command: str, port: int = 8000, timeout: float = 5) -> str:
    """Send one command to the payload and return everything it answers."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(command.encode() + b"\n")
            return sock.makefile("r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        raise ConsoleUnreachable from e


def fetch_firmware(host: str, port: int = 8000) -> str | None:
    """The console's firmware, e.g. "13.42". None from an older payload."""
    for line in fetch_raw(host, "FW", port, timeout=5).splitlines()[1:]:
        key, _, value = line.partition(" ")
        if key == "FW" and value.strip():
            return value.strip()
    return None


if __name__ == "__main__":
    # Quick standalone check against the console.
    logging.basicConfig(level=logging.DEBUG)
    print(fetch_state("192.168.1.18"))