#!/usr/bin/env python3
"""
ToneForge Local Engine - Menu Bar / System Tray App

Runs the local engine server with a system tray icon for easy access.
Works on macOS, Windows, and Linux.

Features:
- Menu bar/system tray icon
- Auto-start on login (optional)
- GPU-accelerated processing

Usage:
    python -m local_engine.tray

Requirements:
    pip install pystray pillow
"""

import io
import logging
import os
import platform
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("toneforge.tray")

# Server state
server_thread = None
server_running = False
current_status = "Stopped"

# Remote worker (cloud link) state. When ~/.toneforge/engine.json (or
# TONEFORGE_BACKEND_URL / TONEFORGE_ENGINE_TOKEN) points at a hosted
# backend like https://jamn.app, a background thread long-polls it for
# analysis jobs and runs them on this machine's GPU.
worker_thread = None
worker_backend = ""
worker_error = ""

# Configuration
HOST = "127.0.0.1"
PORT = 7777
WEB_APP_URL = "http://localhost:8000"
APP_NAME = "ToneForge Local Engine"


# -----------------------------------------------------------------------------
# Auto-Start Management
# -----------------------------------------------------------------------------

def get_app_path() -> str:
    """Get the path to the current executable/script."""
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return sys.executable
    else:
        # Running as script
        return os.path.abspath(__file__)


def is_autostart_enabled() -> bool:
    """Check if auto-start is enabled."""
    system = platform.system()

    if system == "Darwin":  # macOS
        plist_path = Path.home() / "Library/LaunchAgents/com.toneforge.localengine.plist"
        return plist_path.exists()

    elif system == "Windows":
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ
            )
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except WindowsError:
            return False

    elif system == "Linux":
        autostart_path = Path.home() / ".config/autostart/toneforge-local.desktop"
        return autostart_path.exists()

    return False


def enable_autostart():
    """Enable auto-start on login."""
    system = platform.system()
    app_path = get_app_path()

    if system == "Darwin":  # macOS
        plist_dir = Path.home() / "Library/LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist_path = plist_dir / "com.toneforge.localengine.plist"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.toneforge.localengine</string>
    <key>ProgramArguments</key>
    <array>
        <string>{app_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)
        # Load the launch agent
        subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
        logger.info(f"Auto-start enabled: {plist_path}")

    elif system == "Windows":
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{app_path}"')
        winreg.CloseKey(key)
        logger.info("Auto-start enabled via Registry")

    elif system == "Linux":
        autostart_dir = Path.home() / ".config/autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_path = autostart_dir / "toneforge-local.desktop"

        desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Exec={app_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
        desktop_path.write_text(desktop_content)
        logger.info(f"Auto-start enabled: {desktop_path}")


def disable_autostart():
    """Disable auto-start on login."""
    system = platform.system()

    if system == "Darwin":  # macOS
        plist_path = Path.home() / "Library/LaunchAgents/com.toneforge.localengine.plist"
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            plist_path.unlink()
            logger.info("Auto-start disabled")

    elif system == "Windows":
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key)
            logger.info("Auto-start disabled")
        except WindowsError:
            pass

    elif system == "Linux":
        desktop_path = Path.home() / ".config/autostart/toneforge-local.desktop"
        if desktop_path.exists():
            desktop_path.unlink()
            logger.info("Auto-start disabled")


def toggle_autostart():
    """Toggle auto-start setting."""
    if is_autostart_enabled():
        disable_autostart()
    else:
        enable_autostart()


def create_icon_image(color="gray"):
    """Create a simple icon image."""
    from PIL import Image, ImageDraw

    # Create a 64x64 image
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Color based on status
    colors = {
        "gray": (128, 128, 128, 255),
        "green": (45, 122, 78, 255),
        "orange": (184, 80, 26, 255),
        "red": (200, 50, 50, 255),
    }
    fill_color = colors.get(color, colors["gray"])

    # Draw a tuning fork shape
    center_x = size // 2

    # Handle (bottom)
    handle_width = 8
    handle_top = size // 2
    handle_bottom = size - 4
    draw.rectangle(
        [center_x - handle_width // 2, handle_top, center_x + handle_width // 2, handle_bottom],
        fill=fill_color
    )

    # Left prong
    prong_width = 6
    prong_gap = 10
    prong_top = 4
    prong_bottom = handle_top + 4
    draw.rectangle(
        [center_x - prong_gap - prong_width, prong_top, center_x - prong_gap, prong_bottom],
        fill=fill_color
    )
    # Round top
    draw.ellipse(
        [center_x - prong_gap - prong_width, prong_top - 2, center_x - prong_gap, prong_top + prong_width],
        fill=fill_color
    )

    # Right prong
    draw.rectangle(
        [center_x + prong_gap, prong_top, center_x + prong_gap + prong_width, prong_bottom],
        fill=fill_color
    )
    # Round top
    draw.ellipse(
        [center_x + prong_gap, prong_top - 2, center_x + prong_gap + prong_width, prong_top + prong_width],
        fill=fill_color
    )

    # Connect prongs at top
    draw.rectangle(
        [center_x - prong_gap - prong_width, handle_top - 4, center_x + prong_gap + prong_width, handle_top + 4],
        fill=fill_color
    )

    return img


def get_device_info():
    """Get compute device info."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", torch.cuda.get_device_name(0)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps", "Apple Silicon GPU"
        else:
            return "cpu", "CPU"
    except ImportError:
        return "cpu", "CPU (PyTorch not found)"


def start_server():
    """Start the FastAPI server in a background thread."""
    global server_thread, server_running, current_status

    if server_running:
        return

    def run():
        global server_running, current_status
        import uvicorn
        from local_engine.server import app

        server_running = True
        current_status = "Running"
        update_icon("green")

        try:
            uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
        except Exception as e:
            logger.error(f"Server error: {e}")
            current_status = f"Error: {e}"
            update_icon("red")
        finally:
            server_running = False
            current_status = "Stopped"
            update_icon("gray")

    server_thread = threading.Thread(target=run, daemon=True)
    server_thread.start()
    logger.info(f"Server starting on http://{HOST}:{PORT}")


def stop_server():
    """Stop the server (requires restart of app)."""
    global server_running, current_status
    # Note: Uvicorn doesn't have a clean shutdown from another thread
    # The server will stop when the app exits
    current_status = "Stopping... (restart app to fully stop)"
    update_icon("orange")


def start_remote_worker():
    """Start the jamn.app cloud-link worker when configured.

    Configuration comes from TONEFORGE_BACKEND_URL/TONEFORGE_ENGINE_TOKEN
    or ~/.toneforge/engine.json (written by
    ``python -m local_engine.remote_worker --save``). Silently does
    nothing when no backend is configured — local-only use stays
    unchanged.
    """
    global worker_thread, worker_backend, worker_error

    if worker_thread is not None and worker_thread.is_alive():
        return
    try:
        from local_engine.remote_worker import RemoteWorker, load_config
        backend_url, engine_token = load_config()
    except SystemExit:
        return  # not configured — nothing to do
    except Exception as e:  # noqa: BLE001
        worker_error = str(e)
        return

    worker_backend = backend_url

    def run():
        global worker_error
        try:
            RemoteWorker(backend_url, engine_token).run_forever()
        except SystemExit as e:
            worker_error = str(e)
            logger.error(f"cloud link stopped: {e}")
        except Exception as e:  # noqa: BLE001
            worker_error = str(e)
            logger.exception("cloud link crashed")

    worker_thread = threading.Thread(target=run, daemon=True, name="cloud-link")
    worker_thread.start()
    logger.info(f"cloud link polling {backend_url}")


def _worker_state_label() -> str:
    if worker_thread is not None and worker_thread.is_alive():
        return f"connected to {worker_backend.replace('https://', '')}"
    if worker_error:
        return "error (see log)"
    if worker_backend:
        return "stopped"
    return "not configured"


def open_web_app():
    """Open the ToneForge web app in browser."""
    webbrowser.open(WEB_APP_URL)


def open_local_status():
    """Open the local engine status page."""
    webbrowser.open(f"http://{HOST}:{PORT}")


# Global reference to icon for updates
_icon = None


def update_icon(color):
    """Update the tray icon color."""
    global _icon
    if _icon:
        _icon.icon = create_icon_image(color)


def create_menu():
    """Create the system tray menu."""
    import pystray

    device_type, device_name = get_device_info()

    return pystray.Menu(
        pystray.MenuItem(
            lambda text: f"ToneForge Local Engine",
            None,
            enabled=False,
        ),
        pystray.MenuItem(
            lambda text: f"Device: {device_name}",
            None,
            enabled=False,
        ),
        pystray.MenuItem(
            lambda text: f"Status: {current_status}",
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Open ToneForge",
            lambda: open_web_app(),
        ),
        pystray.MenuItem(
            "View Local Engine Status",
            lambda: open_local_status(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Start on Login",
            lambda: toggle_autostart(),
            checked=lambda item: is_autostart_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Start Server",
            lambda: start_server(),
            visible=lambda item: not server_running,
        ),
        pystray.MenuItem(
            "Server Running",
            None,
            enabled=False,
            visible=lambda item: server_running,
        ),
        pystray.Menu.SEPARATOR,
        # Cloud link — jamn.app job worker. Label re-queried each menu
        # draw; the connect item only shows when a backend is configured
        # but the thread isn't running (crash or late config).
        pystray.MenuItem(
            lambda text: f"Cloud link: {_worker_state_label()}",
            None,
            enabled=False,
        ),
        pystray.MenuItem(
            "Connect to jamn.app",
            lambda: start_remote_worker(),
            visible=lambda item: not (worker_thread is not None and worker_thread.is_alive()),
        ),
        pystray.Menu.SEPARATOR,
        # Connect (Swift audio bridge) — supervised as a child of the
        # local engine. We re-query state on every menu draw so the
        # label tracks reality even when the bridge crashes or exits.
        pystray.MenuItem(
            lambda text: f"Audio bridge: {_connect_state_label()}",
            None,
            enabled=False,
        ),
        pystray.MenuItem(
            "Start audio bridge",
            lambda: _connect_start(),
            visible=lambda item: not _connect_is_running(),
        ),
        pystray.MenuItem(
            "Restart audio bridge",
            lambda: _connect_restart(),
            visible=lambda item: _connect_is_running(),
        ),
        pystray.MenuItem(
            "Stop audio bridge",
            lambda: _connect_stop(),
            visible=lambda item: _connect_is_running(),
        ),
        pystray.MenuItem(
            "Open bridge log",
            lambda: _connect_open_log(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Quit",
            lambda icon, item: icon.stop(),
        ),
    )


# ---- Connect bridge tray helpers --------------------------------------------
# These wrap the local_engine.connect_bridge supervisor so the menu can
# render and mutate state without dragging the import into the module
# header (the tray runs even when the server isn't ready yet).

def _connect_supervisor_safe():
    try:
        from local_engine.connect_bridge import get_supervisor
        return get_supervisor()
    except Exception:
        return None


def _connect_is_running() -> bool:
    sup = _connect_supervisor_safe()
    return bool(sup and sup.status().running)


def _connect_state_label() -> str:
    sup = _connect_supervisor_safe()
    if sup is None:
        return "unavailable"
    s = sup.status()
    if s.running:
        return f"running (pid {s.pid})"
    if s.binary is None:
        return "needs build"
    if s.last_error:
        return "stopped"
    return "stopped"


def _connect_start():
    sup = _connect_supervisor_safe()
    if sup is not None:
        sup.start()


def _connect_stop():
    sup = _connect_supervisor_safe()
    if sup is not None:
        sup.stop()


def _connect_restart():
    sup = _connect_supervisor_safe()
    if sup is not None:
        sup.restart()


def _connect_open_log():
    sup = _connect_supervisor_safe()
    if sup is None:
        return
    log_path = sup.status().log_path
    if log_path:
        subprocess.Popen(["open", log_path])


def main():
    """Run the system tray application."""
    global _icon

    try:
        import pystray
    except ImportError:
        print("Installing pystray...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "pystray", "pillow"], check=True)
        import pystray

    from PIL import Image

    device_type, device_name = get_device_info()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           ToneForge Local Engine                             ║
╠══════════════════════════════════════════════════════════════╣
║  Device: {device_name:<50} ║
║  Server: http://{HOST}:{PORT:<40} ║
║                                                              ║
║  Look for the tuning fork icon in your menu bar.            ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Create the icon
    icon_image = create_icon_image("gray")

    _icon = pystray.Icon(
        "toneforge",
        icon_image,
        "ToneForge Local Engine",
        menu=create_menu(),
    )

    # Auto-start the server
    start_server()

    # Auto-start the jamn.app cloud link when configured (no-op otherwise)
    start_remote_worker()

    # Run the icon (blocks)
    _icon.run()


if __name__ == "__main__":
    main()
