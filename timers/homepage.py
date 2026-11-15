from bdh_sdk import ALL_MODES, INFO, ON, WARN, Action, Shortcut, Status, StatusRow

from . import store, worker

ICONS = {
    "pause": ("fa-solid fa-pause", "Pause"),
    "resume": ("fa-solid fa-play", "Resume"),
    "silence": ("fa-solid fa-bell-slash", "Silence"),
    "restart": ("fa-solid fa-undo", "Restart"),
    "remove": ("fa-solid fa-times", "Close"),
}
ROW_ACTIONS = {
    store.RUN: ("pause", "remove"),
    store.PAUSE: ("resume", "remove"),
    store.RING: ("silence", "remove"),
    store.DONE: ("restart", "remove"),
}


def _detail(t):
    if t["state"] == store.RING:
        return "Time's up"
    if t["state"] == store.DONE:
        return "Done"
    if t["state"] == store.PAUSE:
        return f"{store.fmt(store.remaining(t))} · paused"
    return f"{{left}} · ends {store.clock_label(t['end'])}" if t["at"] else "{left}"


def _ends_at(t):
    return t["end"] if t["state"] == store.RUN else None


ORDER = {store.RING: 0, store.RUN: 1, store.PAUSE: 2, store.DONE: 3}


def _rank(t):
    # Ringing first, then running soonest-ending, paused, and finished last.
    return ORDER[t["state"]], t["end"] if t["state"] == store.RUN else 0


def status():
    timers = sorted(store.timers(), key=_rank)
    if not timers:
        return Status("No timers")
    rows = tuple(
        StatusRow(id=t["id"], title=t["name"], detail=_detail(t), ends_at=_ends_at(t), dimmed=t["state"] == store.DONE,
                  actions=tuple(Action(a, *ICONS[a], primary=n == 0)
                                for n, a in enumerate(ROW_ACTIONS[t["state"]])))
        for t in timers)
    ringing = [t for t in timers if t["state"] == store.RING]
    running = sorted((t for t in timers if t["state"] == store.RUN), key=lambda t: t["end"])
    ends_at = None
    if ringing:
        text, level = f"{ringing[0]['name']}: time's up", WARN
    elif running:
        t = running[0]
        text, level, ends_at = f"{t['name']} {{left}}", ON, t["end"]
    else:
        text, level = f"{len(timers)} timer{'s' if len(timers) != 1 else ''}", INFO
    # The screen ticks {left} every second; the poll only catches pauses and timers ending.
    return Status(text, level=level, rows=rows, refresh=2, ends_at=ends_at)


def apply(timer_id, act):
    """Run a timer control and keep alerts and the ticker in step."""
    before = store.get(timer_id)
    t = store.action(timer_id, act)
    if before["state"] == store.RING and (t is None or t["state"] != store.RING):
        worker.silence(timer_id)
    worker.poke()
    return t


def run_action(row_id, action_id, mode=None):
    if action_id not in ICONS:
        raise LookupError(f"unknown action {action_id!r}")
    name = store.get(row_id)["name"]
    apply(row_id, action_id)
    return {"pause": f"Paused {name}", "resume": f"Resumed {name}", "silence": f"Silenced {name}",
            "restart": f"Restarted {name}", "remove": f"Closed {name}"}[action_id]


def shortcuts():
    return [
        Shortcut(id=p["id"], title=p["name"], icon="fa-solid fa-stopwatch", order=60 + n, modes=ALL_MODES)
        for n, p in enumerate(store.presets())
    ]


def run_shortcut(shortcut_id):
    t = store.start_preset(shortcut_id)
    worker.poke()
    return f"Started {t['name']} ({store.fmt(t['total'])})"
