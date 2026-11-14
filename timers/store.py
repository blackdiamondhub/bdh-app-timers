"""Timers and presets, kept in memory and saved to the app's state store.

The hub runs one server process, so a module-level dict under a lock is the
source of truth; every change is written through to ``app_state`` so timers
survive a restart. Times are epoch seconds.

A timer is ``run`` (counting down to ``end``), ``pause`` (``left`` seconds
remain), ``ring`` (just expired, alerting) or ``done`` (finished, waiting to be
closed with the X).
"""
import datetime as dt
import threading
import time
import uuid

from bdh_sdk.storage import app_state

APP = "timers"
MAX_TIMERS = 12
MAX_PRESETS = 12
MAX_NAME = 40
MAX_SECONDS = 24 * 3600
REPEAT_S = 10  # the alert plays once more this long after expiry
RING_S = 30  # how long a timer rings before it settles to "done"

RUN, PAUSE, RING, DONE = "run", "pause", "ring", "done"

_lock = threading.RLock()
_timers = None  # list of dicts, in creation order
_presets = None


def _state():
    return app_state(APP)


def _load():
    global _timers, _presets
    if _timers is None:
        st = _state()
        _timers = [t for t in (st.get("timers") or []) if isinstance(t, dict) and t.get("id")]
        _presets = [p for p in (st.get("presets") or []) if isinstance(p, dict) and p.get("id")]


def _save():
    st = _state()
    st.set("timers", _timers)
    st.set("presets", _presets)


def reset_cache():
    """Forget the in-memory copy (tests, or after the state store changes)."""
    global _timers, _presets
    with _lock:
        _timers = _presets = None


def clean_name(name, fallback="Timer"):
    name = " ".join(str(name or "").split())[:MAX_NAME]
    return name or fallback


def clean_seconds(value):
    try:
        secs = int(value)
    except (TypeError, ValueError):
        raise ValueError("Enter a duration.") from None
    if secs < 1:
        raise ValueError("Enter a duration.")
    if secs > MAX_SECONDS:
        raise ValueError("Timers can run for up to 24 hours.")
    return secs


def _tz():
    try:
        from bdh_sdk.location import tzinfo
        return tzinfo()
    except Exception:
        return None


def end_for(at, now=None):
    """Epoch time of the next ``HH:MM`` (24 h) in the hub's time zone."""
    try:
        hh, mm = (int(x) for x in str(at).split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Enter a time like 18:30.") from None
    now = time.time() if now is None else now
    local = dt.datetime.fromtimestamp(now, _tz())
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target.timestamp() <= now:
        target = (local + dt.timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target.timestamp()


def clock_label(ts):
    """``6:30 PM`` for an epoch time, in the hub's time zone."""
    local = dt.datetime.fromtimestamp(ts, _tz())
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def remaining(t, now=None):
    """Seconds left on ``t`` (0 when ringing or done)."""
    if t["state"] == RUN:
        now = time.time() if now is None else now
        return max(0.0, t["end"] - now)
    if t["state"] == PAUSE:
        return float(t["left"])
    return 0.0


def fmt(secs):
    secs = int(-(-secs // 1))  # round up, so 0:00 only shows when it is over
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _find(timer_id):
    for t in _timers:
        if t["id"] == timer_id:
            return t
    raise LookupError(f"no timer {timer_id!r}")


def timers():
    with _lock:
        _load()
        return [dict(t) for t in _timers]


def get(timer_id):
    with _lock:
        _load()
        return dict(_find(timer_id))


def create(name, seconds=None, at=None, now=None):
    """Start a timer for ``seconds``, or one that ends at ``at`` (``HH:MM``)."""
    now = time.time() if now is None else now
    if at:
        end = end_for(at, now)
        total = max(1, round(end - now))
    else:
        total = clean_seconds(seconds)
        end = now + total
    with _lock:
        _load()
        if len(_timers) >= MAX_TIMERS:
            raise ValueError(f"You can have up to {MAX_TIMERS} timers. Close one first.")
        t = {"id": uuid.uuid4().hex[:8], "name": clean_name(name), "total": total,
             "state": RUN, "end": end, "left": total, "at": bool(at), "rang": None, "repeat": False}
        _timers.append(t)
        _save()
        return dict(t)


def action(timer_id, act, now=None):
    """Apply a control (pause, resume, add, reset, restart, silence, remove).

    Returns the updated timer (``None`` after remove).
    """
    now = time.time() if now is None else now
    with _lock:
        _load()
        t = _find(timer_id)
        st = t["state"]
        if act == "pause" and st == RUN:
            t.update(state=PAUSE, left=max(1, round(t["end"] - now)))
        elif act == "resume" and st == PAUSE:
            t.update(state=RUN, end=now + t["left"])
        elif act == "add":
            if st == RUN:
                t["end"] += 60
                t["total"] += 60
            elif st == PAUSE:
                t["left"] += 60
                t["total"] += 60
            else:  # snooze a finished timer for a minute
                t.update(state=RUN, end=now + 60, total=60, left=60, at=False, rang=None, repeat=False)
        elif act == "reset":
            if t["at"]:
                t["at"] = False
            t.update(state=PAUSE, left=t["total"], rang=None, repeat=False)
        elif act == "restart":
            t.update(state=RUN, end=now + t["total"], left=t["total"], at=False, rang=None, repeat=False)
        elif act == "silence" and st == RING:
            t.update(state=DONE, repeat=False)
        elif act == "remove":
            _timers.remove(t)
            _save()
            return None
        elif act not in ("pause", "resume", "silence"):
            raise ValueError(f"unknown action {act!r}")
        _save()
        return dict(t)


def tick(now=None):
    """Advance timers; returns ``[(event, timer)]`` for the worker to act on.

    Events: ``expire`` (just hit zero), ``repeat`` (play the alert once more)
    and ``done`` (stopped ringing). A timer found long past its end after a
    restart goes straight to done without alerting.
    """
    now = time.time() if now is None else now
    events = []
    with _lock:
        _load()
        for t in _timers:
            if t["state"] == RUN and t["end"] <= now:
                if now - t["end"] > RING_S:
                    t.update(state=DONE, rang=t["end"], repeat=False)
                    continue
                t.update(state=RING, rang=now, repeat=True)
                events.append(("expire", dict(t)))
            elif t["state"] == RING:
                if now - t["rang"] >= RING_S:
                    t.update(state=DONE, repeat=False)
                    events.append(("done", dict(t)))
                elif t["repeat"] and now - t["rang"] >= REPEAT_S:
                    t["repeat"] = False
                    events.append(("repeat", dict(t)))
        if events:
            _save()
    return events


def next_wake(now=None):
    """Seconds until :func:`tick` could next have something to do."""
    now = time.time() if now is None else now
    waits = []
    with _lock:
        _load()
        for t in _timers:
            if t["state"] == RUN:
                waits.append(t["end"] - now)
            elif t["state"] == RING:
                waits.append(min(t["rang"] + (REPEAT_S if t["repeat"] else RING_S) - now, 1))
    return max(0.05, min(waits)) if waits else None


def snapshot(t, now=None):
    """The JSON a screen needs to draw ``t``."""
    now = time.time() if now is None else now
    out = {"id": t["id"], "name": t["name"], "total": t["total"], "state": t["state"],
           "left": round(remaining(t, now), 2), "at": t["at"]}
    if t["state"] == RUN:
        out["ends"] = clock_label(t["end"])
    return out


def presets():
    with _lock:
        _load()
        return [dict(p) for p in _presets]


def add_preset(name, seconds):
    with _lock:
        _load()
        if len(_presets) >= MAX_PRESETS:
            raise ValueError(f"You can save up to {MAX_PRESETS} presets.")
        p = {"id": uuid.uuid4().hex[:8], "name": clean_name(name), "seconds": clean_seconds(seconds)}
        _presets.append(p)
        _save()
        return dict(p)


def delete_preset(preset_id):
    with _lock:
        _load()
        before = len(_presets)
        _presets[:] = [p for p in _presets if p["id"] != preset_id]
        if len(_presets) == before:
            raise LookupError(f"no preset {preset_id!r}")
        _save()


def find_preset(preset_id):
    for p in presets():
        if p["id"] == preset_id:
            return p
    return None


def start_preset(preset_id, now=None):
    p = find_preset(preset_id)
    if p is None:
        raise LookupError(f"no preset {preset_id!r}")
    return create(p["name"], seconds=p["seconds"], now=now)
