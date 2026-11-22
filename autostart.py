"""Opening presence5 when you log in, on Linux and Windows.

- Linux: a presence5.desktop file in ~/.config/autostart, which KDE, GNOME,
  Xfce, Cinnamon and the other desktops open at login.
- Windows: a "presence5" entry under the current user's Run key in the
  registry, the same place Startup apps in Task Manager / Settings lists.

Neither needs administrator rights, and both are removed by turning the
option off again. The entry starts presence5 with --background: hidden in the
tray instead of with its window open.
"""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

BACKGROUND_FLAG = "--background"
NAME = "presence5"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(Exception):
    pass


def supported() -> bool:
    return os.name == "nt" or sys.platform.startswith("linux")


def launch_command() -> list[str]:
    """How to open this copy of presence5 again: the program file itself when it
    is a downloaded build, Python and gui.py when it runs from the source code."""
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable), BACKGROUND_FLAG]
    gui = Path(__file__).resolve().with_name("gui.py")
    return [os.path.abspath(sys.executable), str(gui), BACKGROUND_FLAG]


# --- Linux ------------------------------------------------------------------

def _desktop_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "autostart" / f"{NAME}.desktop"


def _icon_file() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / NAME / f"{NAME}.png"


def _desktop_quote(arg: str) -> str:
    """An argument for the Exec line, quoted the way the desktop entry spec asks."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _write_icon() -> str | None:
    try:
        from logo import LOGO_PNG
        path = _icon_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(LOGO_PNG)
        return str(path)
    except Exception as e:  # the icon is only for the desktop's startup list
        log.debug("could not save the icon for the autostart entry: %r", e)
        return None


def _linux_enable() -> None:
    exec_line = " ".join(_desktop_quote(a) for a in launch_command()).replace("%", "%%")
    icon = _write_icon()
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        "Comment=Discord Rich Presence for the PS5",
        f"Exec={exec_line}",
        *([f"Icon={icon}"] if icon else []),
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "X-KDE-autostart-after=panel",
        "",
    ]
    path = _desktop_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        raise AutostartError(f"could not write {path}: {e.strerror or e}") from e
    log.info("autostart on: %s", path)


def _linux_disable() -> None:
    path = _desktop_file()
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        raise AutostartError(f"could not remove {path}: {e.strerror or e}") from e
    log.info("autostart off")


def _linux_entry() -> str | None:
    try:
        for line in _desktop_file().read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                return line[5:]
    except OSError:
        pass
    return None


# --- Windows ----------------------------------------------------------------

def _windows_command() -> str:
    import subprocess
    return subprocess.list2cmdline(launch_command())


def _windows_enable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, _windows_command())
    except OSError as e:
        raise AutostartError(f"could not add presence5 to the startup apps: {e}") from e
    log.info("autostart on: HKCU\\%s\\%s", RUN_KEY, NAME)


def _windows_disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, NAME)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise AutostartError(f"could not remove presence5 from the startup apps: {e}") from e
    log.info("autostart off")


def _windows_entry() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, NAME)[0]
    except OSError:
        return None


# --- Both -------------------------------------------------------------------

def is_enabled() -> bool:
    if os.name == "nt":
        return _windows_entry() is not None
    if sys.platform.startswith("linux"):
        return _linux_entry() is not None
    return False


def set_enabled(on: bool) -> None:
    if not supported():
        raise AutostartError("opening at login isn't supported on this system")
    if os.name == "nt":
        _windows_enable() if on else _windows_disable()
    else:
        _linux_enable() if on else _linux_disable()


def refresh() -> None:
    """When the option is on, point the entry at this copy of presence5, so it
    keeps working after the program file is moved or replaced by a new version."""
    if not supported() or not is_enabled():
        return
    current = _windows_entry() if os.name == "nt" else _linux_entry()
    wanted = _windows_command() if os.name == "nt" else \
        " ".join(_desktop_quote(a) for a in launch_command()).replace("%", "%%")
    if current != wanted:
        try:
            set_enabled(True)
            log.info("autostart entry updated to this copy of presence5")
        except AutostartError as e:
            log.warning("%s", e)
