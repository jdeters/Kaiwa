"""Phone access: Tailscale status for the 'use it on your phone' guide.

The phone is just a screen; this computer does the work. Phone browsers only
allow the microphone over HTTPS, which Tailscale's link provides.
"""
import json
import subprocess


_TAILSCALE_PATHS = [
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS app bundle
    "tailscale",                                             # PATH (brew, Linux)
    r"C:\Program Files\Tailscale\tailscale.exe",             # Windows default
]


def phone_info() -> dict:
    """Tailscale status for the 'use Kaiwa on your phone' guide."""
    for ts in _TAILSCALE_PATHS:
        try:
            out = subprocess.check_output([ts, "status", "--json"], timeout=5,
                                          stderr=subprocess.DEVNULL)
            st = json.loads(out)
            running = st.get("BackendState") == "Running"
            dns = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
            return {"installed": True, "running": running,
                    "url": f"https://{dns}" if running and dns else None}
        except FileNotFoundError:
            continue
        except Exception:
            return {"installed": True, "running": False, "url": None}
    return {"installed": False, "running": False, "url": None}
