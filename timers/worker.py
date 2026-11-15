"""Background ticker: rings timers when they run out.

On expiry it pushes a hub alert (chime + "<name> has expired" on every
screen), plays it once more ``REPEAT_S`` later, and optionally announces it on
house speakers. ``RING_S`` after expiry the timer settles to "done".
"""
import logging
import os
import sys
import threading

from bdh_sdk import alerts
from bdh_sdk.settings import app_setting

from . import store

log = logging.getLogger(__name__)

VOICE = "AvaNeural"  # same voice the announcements app uses

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


def _is_runtime(path):
    """``python -m bdh_sdk.runtime`` (the app's container) runs ``bdh_sdk/runtime/__main__.py``."""
    parts = path.replace("\\", "/").split("/")
    return parts[-3:] == ["bdh_sdk", "runtime", "__main__.py"]


def should_start(argv=None):
    """Start only in the server process (the app's container, Daphne, or runserver's reloaded child)."""
    if os.environ.get("BDH_TIMERS_WORKER", "").lower() in ("0", "false", "off", "no"):
        return False
    argv = sys.argv if argv is None else argv
    if argv and _is_runtime(argv[0]):
        return True
    prog = os.path.basename(argv[0]) if argv else ""
    if "daphne" in prog:
        return True
    if len(argv) > 1 and prog in ("manage.py", "django-admin", "__main__.py"):
        if argv[1] == "runserver":
            return os.environ.get("RUN_MAIN") == "true" or "--noreload" in argv
        return False
    return False


def chime():
    value = app_setting(store.APP, "chime", "soft-bell")
    return value if value in alerts.CHIMES or value == alerts.SILENT else "soft-bell"


def speakers():
    from bdh_sdk.plugins import as_list
    raw = app_setting(store.APP, "speakers", None)
    return as_list(raw) if raw else []


def targets_for(t):
    """The timer's own speakers, or the app's when it has none set."""
    own = t.get("speakers")
    return list(own) if own is not None else speakers()


def volume():
    return app_setting(store.APP, "volume", 50, cast=int)


def message(t):
    return f"{t['name']} has expired"


def alert_key(timer_id):
    return f"timer:{timer_id}"


def ring(t, repeat=False):
    alerts.push(alerts.Alert(
        app=store.APP, key=alert_key(t["id"]), title=t["name"], body="Time's up",
        embed_url=f"/timers/popup/{t['id']}/", ttl=store.RING_S + 15, sound=chime(),
        speak=message(t)[:alerts.MAX_SPEAK], resound=repeat))
    targets = targets_for(t)
    if targets:
        threading.Thread(target=_announce, args=(message(t), targets), daemon=True,
                         name="timers-announce").start()


def _announce(text, targets):
    from bdh_sdk import speakers as sp
    try:
        snd = chime()
        sp.announce(text, targets, volume=volume(), chime=None if snd == alerts.SILENT else snd,
                    voice=VOICE)
    except Exception:
        log.exception("timers: speaker announcement failed")


def silence(timer_id):
    alerts.dismiss(store.APP, alert_key(timer_id))


def handle(events):
    for event, t in events:
        try:
            if event == "expire":
                ring(t)
            elif event == "repeat":
                ring(t, repeat=True)
            elif event == "done":
                silence(t["id"])
        except Exception:
            log.exception("timers: %s for %s failed", event, t.get("id"))


def poke():
    """Wake the ticker after a change so a new deadline is picked up."""
    _wake.set()


def _run():
    while True:
        try:
            handle(store.tick())
            wait = store.next_wake()
        except Exception:
            log.exception("timers: tick failed")
            wait = 1
        _wake.wait(min(wait, 1.0) if wait is not None else 30)
        _wake.clear()


def start():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, daemon=True, name="timers-worker").start()
