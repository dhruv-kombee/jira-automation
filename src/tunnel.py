import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional
import httpx
from src.logger import logger

NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"
_ngrok_process: Optional[subprocess.Popen] = None

# Known fallback paths on Windows
FALLBACK_NGROK_PATHS = [
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe")),
    Path(os.path.expandvars(r"%PROGRAMFILES%\ngrok\ngrok.exe")),
    Path(os.path.expandvars(r"%USERPROFILE%\ngrok.exe")),
    Path(os.path.expandvars(r"%USERPROFILE%\Downloads\ngrok.exe")),
    Path(os.path.expandvars(r"%USERPROFILE%\Desktop\ngrok.exe")),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\ngrok\ngrok.exe")),
    Path("ngrok.exe"),
]


def find_ngrok_binary() -> Optional[str]:
    """Locate the ngrok executable on the system."""
    which_path = shutil.which("ngrok")
    if which_path:
        return which_path

    for fallback in FALLBACK_NGROK_PATHS:
        if fallback.exists():
            return str(fallback)

    return None


def get_active_tunnel_url() -> Optional[str]:
    """Check if ngrok is already running and return the public HTTPS URL."""
    try:
        response = httpx.get(NGROK_API_URL, timeout=1.5)
        if response.is_success:
            data = response.json()
            tunnels = data.get("tunnels", [])
            for tunnel in tunnels:
                public_url = tunnel.get("public_url", "")
                if public_url.startswith("https://"):
                    return public_url
            if tunnels:
                return tunnels[0].get("public_url")
    except Exception:
        pass
    return None


def ensure_tunnel(port: int = 3000) -> Optional[str]:
    """Ensure a public ngrok tunnel is running and return its HTTPS URL.

    If ngrok is already running, returns its existing URL.
    If not, starts ngrok in the background.
    """
    global _ngrok_process

    # 1. Check if already running
    existing_url = get_active_tunnel_url()
    if existing_url:
        logger.info(f"Connected to existing ngrok tunnel: {existing_url}", extra={"event": "TUNNEL_FOUND"})
        return existing_url

    # 2. Find binary
    binary = find_ngrok_binary()
    if not binary:
        logger.warning(
            "ngrok executable not found in PATH or standard directories. Please start ngrok manually.",
            extra={"event": "TUNNEL_NOT_FOUND"},
        )
        return None

    # 3. Start ngrok process
    logger.info(f"Starting ngrok on port {port}...", extra={"event": "TUNNEL_START"})
    try:
        # Start detached in background on Windows
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW

        _ngrok_process = subprocess.Popen(
            [binary, "http", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )

        # Wait for tunnel to come online (up to 6 seconds)
        for _ in range(12):
            time.sleep(0.5)
            url = get_active_tunnel_url()
            if url:
                logger.info(f"ngrok tunnel established: {url}", extra={"event": "TUNNEL_ONLINE", "url": url})
                return url

    except Exception as err:
        logger.error(f"Failed to start ngrok: {err}", extra={"event": "TUNNEL_ERROR"})

    return None


def stop_tunnel():
    """Terminate the ngrok subprocess if we started it."""
    global _ngrok_process
    if _ngrok_process is not None:
        try:
            logger.info("Stopping background ngrok process...", extra={"event": "TUNNEL_STOP"})
            _ngrok_process.terminate()
            _ngrok_process.wait(timeout=2)
        except Exception:
            try:
                _ngrok_process.kill()
            except Exception:
                pass
        _ngrok_process = None

