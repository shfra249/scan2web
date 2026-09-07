"""
Scanner Bridge Service
=======================
A local HTTP (Flask) bridge to a Windows-attached scanner using the built-in
WIA (Windows Image Acquisition) layer.

Features
--------
- GET  /status  -> connection info (name, flatbed/ADF support, last error)
- POST /scan    -> scans the document and returns it as base64 PNG
                   automatically decides Flatbed vs ADF unless told otherwise
- System tray icon: green = scanner connected, red = not connected/error
- Friendly error messages for common WIA error codes (jam, no paper, busy,
  offline, cover open, warming up, etc.)
- Designed to be frozen into a single .exe with PyInstaller

Requirements (Windows only, WIA is a Windows API):
    pip install -r requirements.txt

Run:
    python scanner_service.py

Build exe:
    build.bat   (see that file / README.md)
"""

import base64
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Optional, Tuple

from flask import Flask, jsonify, request

# ---- Optional / platform-specific imports -------------------------------
try:
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover - only true on non-Windows / missing pywin32
    pythoncom = None
    win32com = None

try:
    import winreg as reg
except ImportError:  # pragma: no cover - winreg only exists on Windows
    reg = None

try:
    import pystray
    from PIL import Image
except ImportError:  # pragma: no cover
    pystray = None
    Image = None

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 5000
POLL_INTERVAL_SECONDS = 4          # how often the tray icon / status refreshes
DEFAULT_DPI = 200

def _base_dir() -> str:
    if getattr(sys, "frozen", False):          # running as a PyInstaller exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(filename: str) -> str:
    """
    Resolve a bundled resource (e.g. an icon) so it works both when run as a
    plain .py script and when frozen into a PyInstaller --onefile exe (where
    bundled data files are unpacked into sys._MEIPASS at runtime).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(_base_dir(), filename)


GREEN_ICON_FILE = resource_path("green.ico")
RED_ICON_FILE = resource_path("red.ico")

# No file logging: a log file left running indefinitely would grow forever
# and could eventually slow the machine down. Current status is available
# in-memory at all times via GET /status instead (see get_state() below).
# If a console is attached (i.e. not a --windowed frozen exe) messages are
# still printed there for convenience; otherwise logging is a silent no-op.
logging.basicConfig(level=logging.CRITICAL + 1)  # effectively disables the root logger
log = logging.getLogger("scanner_service")
log.setLevel(logging.INFO)
log.propagate = False
if sys.stdout is not None:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_console_handler)
else:
    log.addHandler(logging.NullHandler())

# ---------------------------------------------------------------------
# WIA constants
#   (from the public WIA Automation Layer / wiadef.h reference)
# ---------------------------------------------------------------------
WIA_DEVICE_TYPE_SCANNER = 1

WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
WIA_DPS_DOCUMENT_HANDLING_STATUS = 3087

WIA_IPS_XRES = 6147
WIA_IPS_YRES = 6148

FEEDER_FLAG = 0x0001        # device has an ADF
FLATBED_FLAG = 0x0002       # device has a flatbed
FEED_READY_FLAG = 0x0001    # paper is currently loaded in the ADF

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

# Common WIA / scanner HRESULT error codes -> human-readable messages
WIA_ERROR_MESSAGES = {
    0x80210001: "General scanner error. Try restarting the scanner.",
    0x80210002: "Scanner is not ready. Check the connection and try again.",
    0x80210003: "No paper found in the automatic document feeder (ADF).",
    0x80210004: "The paper is jammed inside the scanner.",
    0x80210005: "Scanner lamp is off or still warming up.",
    0x80210006: "Paper jam detected in the feeder.",
    0x80210007: "Scanner is out of paper.",
    0x80210008: "A device I/O error occurred while scanning.",
    0x80210009: "Scanner cover/lid is open.",
    0x8021000A: "Scanner is busy with another request.",
    0x8021000B: "Scanner is warming up, please retry in a few seconds.",
    0x8021000C: "Scanner is offline or powered off.",
    0x8021000D: "Requested scan settings are not supported by this device.",
    0x80210016: "Paper problem detected (misfeed, skew, or multi-feed).",
    0x80210064: "General device communication error.",
    0x80070005: "Access denied talking to the scanner (permissions/driver issue).",
}

app = Flask(__name__)

# ---------------------------------------------------------------------
# Shared status state (used by both the HTTP API and the tray icon)
# ---------------------------------------------------------------------
_state_lock = threading.Lock()
_state = {
    "connected": False,
    "device_name": None,
    "supports_flatbed": False,
    "supports_adf": False,
    "last_error": None,
    "last_checked": None,
}


def set_state(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def get_state() -> dict:
    with _state_lock:
        return dict(_state)


# ---------------------------------------------------------------------
# WIA helper functions
# ---------------------------------------------------------------------
class ScannerError(RuntimeError):
    """Raised for any scanner-related failure with a user-friendly message."""


def _require_windows():
    if win32com is None:
        raise ScannerError(
            "pywin32 is not installed or this is not running on Windows. "
            "WIA scanning only works on Windows. Install with: pip install pywin32"
        )


def _com_error_to_message(exc: Exception) -> str:
    try:
        hresult = getattr(exc, "hresult", None)
        if hresult is None and hasattr(exc, "args") and exc.args:
            hresult = exc.args[0]
        code = hresult & 0xFFFFFFFF if isinstance(hresult, int) else None
        if code in WIA_ERROR_MESSAGES:
            return WIA_ERROR_MESSAGES[code]
        if code is not None:
            return f"Scanner error (0x{code:08X})."
        return f"Scanner error: {exc}"
    except Exception:
        return f"Scanner error: {exc}"


def _get_wia_manager():
    _require_windows()
    pythoncom.CoInitialize()
    return win32com.client.Dispatch("WIA.DeviceManager")


def _has_prop(obj, name_or_id) -> bool:
    try:
        obj.Properties.Item(name_or_id)
        return True
    except Exception:
        return False


def _get_prop(obj, name_or_id, default=None):
    try:
        return obj.Properties.Item(name_or_id).Value
    except Exception:
        return default


def find_first_scanner():
    """Return (device, device_info) for the first available WIA scanner, or (None, None)."""
    manager = _get_wia_manager()
    count = manager.DeviceInfos.Count
    for i in range(1, count + 1):
        info = manager.DeviceInfos.Item(i)
        if info.Type == WIA_DEVICE_TYPE_SCANNER:
            try:
                device = info.Connect()
                return device, info
            except Exception as exc:
                log.warning("Found scanner but could not connect: %s", _com_error_to_message(exc))
                continue
    return None, None


def detect_capabilities(device) -> Tuple[bool, bool]:
    """Return (supports_flatbed, supports_adf) based on device capability flags."""
    caps = _get_prop(device, WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, None)
    if caps is None:
        # Some very simple flatbed-only scanners don't expose this property at all.
        return True, False
    supports_flatbed = bool(caps & FLATBED_FLAG)
    supports_adf = bool(caps & FEEDER_FLAG)
    return supports_flatbed, supports_adf


def adf_has_paper(device) -> bool:
    """True if the ADF currently reports paper loaded and ready to feed."""
    status = _get_prop(device, WIA_DPS_DOCUMENT_HANDLING_STATUS, None)
    if status is None:
        return False
    return bool(status & FEED_READY_FLAG)


def get_scan_item(device, source: str):
    """
    Return the WIA Item to scan from.
    source is 'flatbed' or 'adf'. WIA devices normally expose child items
    named e.g. 'Flatbed' and 'Feeder'; we match by name, falling back to
    positional index (1 = flatbed, 2 = feeder) if names aren't available.
    """
    wanted = "feed" if source == "adf" else "flat"
    for i in range(1, device.Items.Count + 1):
        item = device.Items.Item(i)
        name = ""
        if _has_prop(item, "Item Name"):
            name = str(_get_prop(item, "Item Name", "")).lower()
        if wanted in name:
            return item

    # Fallback when the driver doesn't expose readable item names
    if wanted == "feed" and device.Items.Count >= 2:
        return device.Items.Item(2)
    return device.Items.Item(1)


def set_item_property(item, prop_id, value) -> None:
    """Best-effort property set; silently skipped if unsupported by the driver."""
    try:
        item.Properties.Item(prop_id).Value = value
    except Exception as exc:
        log.debug("Could not set property %s=%s: %s", prop_id, value, exc)


def perform_scan(dpi: int = DEFAULT_DPI, source: str = "auto") -> Tuple[bytes, str]:
    """
    Perform a scan and return (png_bytes, source_actually_used).
    source: 'auto' (default), 'flatbed', or 'adf'.

    Auto-detection logic:
      1. If the device has an ADF AND paper is currently loaded in it -> use ADF.
      2. Otherwise, if it has a flatbed -> use flatbed.
      3. Otherwise fall back to whatever is supported.

    NOTE: some WIA drivers ignore the requested FormatID and always hand back
    native BMP data regardless of what was asked for. To guarantee callers
    always get a real PNG (matching the "format": "png" field in the API
    response), the raw bytes are inspected and, if they aren't already a PNG,
    converted with Pillow before being returned.
    """
    device, info = find_first_scanner()
    if device is None:
        raise ScannerError("No scanner found. Check that it is powered on and connected.")

    supports_flatbed, supports_adf = detect_capabilities(device)

    if source == "auto":
        if supports_adf and adf_has_paper(device):
            chosen = "adf"
        elif supports_flatbed:
            chosen = "flatbed"
        elif supports_adf:
            chosen = "adf"
        else:
            raise ScannerError("Scanner reports no usable source (no flatbed or ADF detected).")
    elif source == "adf":
        if not supports_adf:
            raise ScannerError("This scanner does not have an automatic document feeder (ADF).")
        if not adf_has_paper(device):
            raise ScannerError("No paper detected in the ADF feeder.")
        chosen = "adf"
    elif source == "flatbed":
        if not supports_flatbed:
            raise ScannerError("This scanner does not have a flatbed.")
        chosen = "flatbed"
    else:
        raise ValueError("source must be one of: auto, flatbed, adf")

    item = get_scan_item(device, chosen)

    set_item_property(item, WIA_IPS_XRES, dpi)
    set_item_property(item, WIA_IPS_YRES, dpi)

    try:
        image = item.Transfer(WIA_FORMAT_PNG)
    except Exception as exc:  # pywintypes.com_error
        raise ScannerError(_com_error_to_message(exc)) from exc

    tmp_path = os.path.join(tempfile.gettempdir(), f"scan_{int(time.time() * 1000)}.png")
    try:
        image.SaveFile(tmp_path)
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    data = _ensure_png(data)
    return data, chosen


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _ensure_png(data: bytes) -> bytes:
    """
    Some WIA drivers ignore the requested FormatID and return native BMP
    bytes even when WIA_FORMAT_PNG was requested. Detect that and convert
    to a real PNG so the API's "format": "png" claim is always true.
    """
    if data[:8] == PNG_SIGNATURE:
        return data  # already a real PNG, nothing to do

    try:
        import io
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as img:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as exc:
        log.warning("Could not convert scanner output to PNG (%s); returning raw bytes.", exc)
        return data


def refresh_scanner_status() -> None:
    """Poll the scanner and update the shared state dict. Safe to call often."""
    try:
        device, info = find_first_scanner()
        if device is None:
            set_state(
                connected=False,
                device_name=None,
                supports_flatbed=False,
                supports_adf=False,
                last_error="No scanner detected",
                last_checked=time.time(),
            )
            return
        supports_flatbed, supports_adf = detect_capabilities(device)
        name = _get_prop(info, "Name", "Unknown scanner")
        set_state(
            connected=True,
            device_name=name,
            supports_flatbed=supports_flatbed,
            supports_adf=supports_adf,
            last_error=None,
            last_checked=time.time(),
        )
    except Exception as exc:
        set_state(
            connected=False,
            device_name=None,
            last_error=_com_error_to_message(exc),
            last_checked=time.time(),
        )


def status_poll_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        refresh_scanner_status()
        stop_event.wait(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------
@app.route("/status", methods=["GET"])
def status():
    return jsonify(get_state())


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/scan", methods=["POST", "GET"])
def scan():
    """
    Scan a page. Works with a completely empty POST body - just POST with no
    payload and it scans using the defaults (auto source, DEFAULT_DPI).
    Optional overrides (JSON body or query string): {"dpi": 300, "source": "adf"}.
    """
    payload = request.get_json(silent=True, force=True) or {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        dpi = int(payload.get("dpi") or request.args.get("dpi") or DEFAULT_DPI)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "dpi must be an integer"}), 400

    source = str(payload.get("source") or request.args.get("source") or "auto").lower()
    if source not in ("auto", "flatbed", "adf"):
        return jsonify({"success": False, "error": "source must be one of: auto, flatbed, adf"}), 400

    try:
        image_bytes, used_source = perform_scan(dpi=dpi, source=source)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        refresh_scanner_status()
        return jsonify({
            "success": True,
            "source_used": used_source,
            "dpi": dpi,
            "format": "png",
            "image_base64": b64,
        })
    except ScannerError as exc:
        log.error("Scan failed: %s", exc)
        refresh_scanner_status()
        return jsonify({"success": False, "error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        log.exception("Unexpected scan error")
        refresh_scanner_status()
        return jsonify({"success": False, "error": f"Unexpected error: {exc}"}), 500


def run_flask() -> None:
    from werkzeug.serving import run_simple
    run_simple(HOST, PORT, app, use_reloader=False, threaded=True)


# ---------------------------------------------------------------------
# System tray icon (green.ico = connected, red.ico = disconnected)
# ---------------------------------------------------------------------
def load_icon(path: str, fallback_color: str):
    """Load an .ico file for the tray icon; fall back to a plain colored dot
    if the file is missing so the app doesn't crash without the assets."""
    try:
        return Image.open(path)
    except Exception as exc:
        log.warning("Could not load icon '%s' (%s); using a fallback dot.", path, exc)
        from PIL import ImageDraw
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = 6
        draw.ellipse((margin, margin, size - margin, size - margin), fill=fallback_color, outline="white", width=3)
        return img


def build_tray(stop_event: threading.Event):
    if pystray is None:
        log.warning("pystray/Pillow not installed; running without a tray icon.")
        return None

    green_icon = load_icon(GREEN_ICON_FILE, "#2ecc71")
    red_icon = load_icon(RED_ICON_FILE, "#e74c3c")

    def on_open(icon, item):
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}/status")

    def on_quit(icon, item):
        stop_event.set()
        icon.stop()
        os._exit(0)

    tray = pystray.Icon(
        "scanner_bridge",
        icon=red_icon,
        title="Scanner Bridge - checking...",
        menu=pystray.Menu(
            pystray.MenuItem("Open status page", on_open),
            pystray.MenuItem("Quit", on_quit),
        ),
    )

    def updater():
        while not stop_event.is_set():
            state = get_state()
            if state["connected"]:
                tray.icon = green_icon
                tray.title = f"Scanner Bridge - Connected: {state['device_name']}"
            else:
                reason = state.get("last_error") or "No scanner found"
                tray.icon = red_icon
                tray.title = f"Scanner Bridge - Disconnected ({reason})"
            stop_event.wait(POLL_INTERVAL_SECONDS)

    threading.Thread(target=updater, daemon=True).start()
    return tray


# ---------------------------------------------------------------------
# Windows startup registration
# ---------------------------------------------------------------------
def register_to_windows_startup():
    """Automatically registers the executable to run on Windows boot."""
    try:
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            exe_path = os.path.abspath(__file__)
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, key_path, 0, reg.KEY_SET_VALUE)
        reg.SetValueEx(key, "LocalScannerAPI", 0, reg.REG_SZ, exe_path)
        reg.CloseKey(key)
    except Exception:
        pass


def main() -> None:
    register_to_windows_startup()
    stop_event = threading.Event()

    threading.Thread(target=status_poll_loop, args=(stop_event,), daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()

    log.info("Scanner Bridge Service running on http://127.0.0.1:%s", PORT)

    tray = build_tray(stop_event)
    if tray is not None:
        tray.run()  # blocking; must run on the main thread
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()


if __name__ == "__main__":
    main()
