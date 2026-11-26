"""Assistant tools: what the hub's voice/chat assistant can ask or do here.

They mirror the page, which every mode can use (guests run kitchen timers too):
list the timers, start one (a duration or an "ends at" alarm) or a saved preset,
pause, resume, add or take off time, cancel, and stop a ringing timer. Answers
are short sentences the assistant can read out.
"""
from __future__ import annotations

import datetime as dt
import time

from bdh_sdk import ALL_MODES
from bdh_sdk.tools import MAX_ENUM, Tool, ToolError

from . import homepage, store, worker

LIST_TIMERS = "list_timers"
START_TIMER = "start_timer"
START_PRESET = "start_preset"
PAUSE_TIMER = "pause_timer"
RESUME_TIMER = "resume_timer"
ADD_TIME = "add_time"
CANCEL_TIMER = "cancel_timer"
STOP_ALARM = "stop_alarm"

DEFAULT_TIMER = "Timer"
DEFAULT_ALARM = "Alarm"
MAX_HOURS = store.MAX_SECONDS // 3600
MAX_MINUTES = store.MAX_SECONDS // 60

clock = time.time  # tests patch this


# ── tool list ──

def _timer_param(what):
    return {"type": "string", "maxLength": 60,
            "description": f"Which timer{what}. Map the user's words to one of the timer names (list_timers "
                           "has them); if it's unclear which one they mean, ask before acting. Leave it out "
                           "when there's only one timer."}


def _int(lo, hi, what):
    return {"type": "integer", "minimum": lo, "maximum": hi, "description": what}


def _params(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def tools(mode=None):
    names = list(dict.fromkeys(p["name"] for p in store.presets()))
    preset = {"type": "string", "description": "The preset's name"}
    if 0 < len(names) <= MAX_ENUM:
        preset["enum"] = names
    timer = _timer_param("")
    return [
        Tool(LIST_TIMERS, "List timers",
             "The kitchen timers and alarms: which are running, paused or ringing and how long each has "
             "left (or when an alarm rings), plus the saved presets. Use it to answer questions about the "
             "timers and to learn their names before changing one.",
             params=_params({}), modes=ALL_MODES, readonly=True),
        Tool(START_TIMER, "Start a timer",
             "Start a named kitchen timer for a duration (hours, minutes and/or seconds), or an alarm that "
             "rings at a clock time (ends_at), not both. Every screen chimes and announces its name when "
             f"it runs out. Up to {store.MAX_TIMERS} timers, each up to 24 hours.",
             params=_params({
                 "name": {"type": "string", "maxLength": store.MAX_NAME,
                          "description": "A short name, like Pasta or Taxi; the user's words for it. "
                                         "Leave it out if they didn't give one."},
                 "hours": _int(0, MAX_HOURS, "Hours"),
                 "minutes": _int(0, MAX_MINUTES, "Minutes"),
                 "seconds": _int(0, store.MAX_SECONDS, "Seconds"),
                 "ends_at": {"type": "string", "minLength": 3, "maxLength": 5,
                             "description": "For an alarm instead of a duration: the local time it rings, "
                                            "24-hour HH:MM (6:30 PM is 18:30). The next such time, today "
                                            "or tomorrow."},
             }),
             modes=ALL_MODES),
        Tool(START_PRESET, "Start a preset timer",
             "Start a timer from a saved preset (a name and a duration saved on the Timers page, like "
             "Egg timer). list_timers names the presets.",
             params=_params({"preset": preset}, required=["preset"]), modes=ALL_MODES),
        Tool(PAUSE_TIMER, "Pause a timer", "Pause a running timer; resume_timer carries on from there.",
             params=_params({"timer": timer}), modes=ALL_MODES),
        Tool(RESUME_TIMER, "Resume a timer", "Resume a paused timer.",
             params=_params({"timer": timer}), modes=ALL_MODES),
        Tool(ADD_TIME, "Add time to a timer",
             "Add time to a timer, or take time off with negative numbers. On a timer that has finished or "
             "is ringing, it stops the ringing and runs again for that long (a snooze).",
             params=_params({
                 "minutes": _int(-MAX_MINUTES, MAX_MINUTES, "Minutes to add (negative to take off)"),
                 "seconds": _int(-store.MAX_SECONDS, store.MAX_SECONDS,
                                 "Seconds to add (negative to take off)"),
                 "timer": timer,
             }),
             modes=ALL_MODES),
        Tool(CANCEL_TIMER, "Cancel a timer",
             "Cancel a timer or alarm and remove it from the screens, like closing it with the X. Also "
             "clears away finished timers.",
             params=_params({"timer": timer}), modes=ALL_MODES),
        Tool(STOP_ALARM, "Stop a ringing timer",
             "Silence a ringing timer on every screen, like its Stop button. Without a timer name it stops "
             "every ringing timer. The timer stays on screen as finished.",
             params=_params({"timer": _timer_param(" to stop")}), modes=ALL_MODES),
    ]


# ── speech ──

def _plural(n, unit):
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _amount(secs):
    """``4 minutes 30 seconds`` (hours and minutes once it's an hour or more)."""
    secs = max(0, int(-(-secs // 1)))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    if h:
        return " ".join([_plural(h, "hour")] + ([_plural(m, "minute")] if m else []))
    if m:
        return " ".join([_plural(m, "minute")] + ([_plural(s, "second")] if s else []))
    return _plural(s, "second")


def _when(ts, now):
    """``at 6:30 PM``, or ``tomorrow at 7:00 AM``, in the hub's time zone."""
    tz = store._tz()
    day = (dt.datetime.fromtimestamp(ts, tz).date() - dt.datetime.fromtimestamp(now, tz).date()).days
    prefix = {0: "", 1: "tomorrow "}.get(day, "")
    return f"{prefix}at {store.clock_label(ts)}"


def _join(items, word):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {word} " + items[-1]


def _describe(t, now):
    name = t["name"]
    if t["state"] == store.RING:
        return f"{name} is ringing."
    if t["state"] == store.DONE:
        return f"{name} has finished."
    left = _amount(store.remaining(t, now))
    if t["state"] == store.PAUSE:
        return f"{name} is paused with {left} left."
    if t["at"]:
        return f"{name} rings {_when(t['end'], now)}, in {left}."
    return f"{name} has {left} left."


# ── running ──

def _norm(name):
    key = " ".join(str(name).split()).casefold().removeprefix("the ")
    for suffix in (" timer", " alarm"):
        key = key.removesuffix(suffix)
    return key


def _timers():
    return sorted(store.timers(), key=homepage._rank)


def _names(timers):
    return list(dict.fromkeys(t["name"] for t in timers))


def _pick(wanted, fits=None):
    """The timer the user means. ``fits`` narrows an unnamed or ambiguous pick to timers it applies to."""
    timers = _timers()
    if not timers:
        raise ToolError("There are no timers.")
    if wanted:
        # Normalise both sides: a timer may itself be called "Pasta Timer".
        key = _norm(wanted)
        hits = [t for t in timers if _norm(t["name"]) == key]
        if not hits:
            raise ToolError(f"There's no timer called {wanted!r}; the timers are "
                            f"{_join(_names(timers), 'and')}.")
    else:
        hits = timers
    if len(hits) > 1 and fits is not None:
        narrowed = [t for t in hits if fits(t)]
        if len(narrowed) == 1:
            return narrowed[0]
    if len(hits) == 1:
        return hits[0]
    if wanted:
        raise ToolError(f"There are {len(hits)} timers called {hits[0]['name']}; "
                        "pick one on the Timers page.")
    raise ToolError(f"Which timer: {_join(_names(timers), 'or')}?")


def _unique_name(name, now):
    """``name``, closing a finished timer of that name, or numbered if it's still in use."""
    key = _norm(name)
    for t in _timers():
        if t["state"] == store.DONE and _norm(t["name"]) == key:
            homepage.apply(t["id"], "remove", now=now)
    taken = {_norm(t["name"]) for t in store.timers()}
    if key not in taken:
        return name
    n = 2
    while _norm(f"{name} {n}") in taken:
        n += 1
    suffix = f" {n}"
    return name[:store.MAX_NAME - len(suffix)] + suffix


def _create(name, now, *, seconds=None, at=None, speakers=None):
    if len(store.timers()) >= store.MAX_TIMERS and not any(
            t["state"] == store.DONE and _norm(t["name"]) == _norm(name) for t in store.timers()):
        raise ToolError(f"There are already {store.MAX_TIMERS} timers, the most there can be. "
                        "Cancel one first.")
    name = _unique_name(name, now)
    try:
        t = store.create(name, seconds=seconds, at=at, now=now, speakers=speakers)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    worker.poke()
    return t


def _list_timers(args, now):
    timers = _timers()
    text = " ".join(_describe(t, now) for t in timers) if timers else "There are no timers."
    presets = store.presets()
    if presets:
        text += " Saved presets: " + "; ".join(f"{p['name']}, {_amount(p['seconds'])}" for p in presets) + "."
    return text


def _start_timer(args, now):
    at = args.get("ends_at")
    duration = [k for k in ("hours", "minutes", "seconds") if k in args]
    if at and duration:
        raise ToolError("Give either how long the timer runs or when it rings, not both.")
    given = " ".join(str(args.get("name") or "").split())
    if at:
        try:
            store.end_for(at.strip(), now)
        except ValueError:
            raise ToolError(f"{at!r} isn't a time: give ends_at as 24-hour HH:MM, like 18:30.") from None
        t = _create(store.clean_name(given, DEFAULT_ALARM), now, at=at.strip())
        when = f"{_when(t['end'], now)}, in {_amount(t['end'] - now)}"
        if given:
            return f"{t['name']} will ring {when}."
        called = "" if t["name"] == DEFAULT_ALARM else f", called {t['name']}"
        return f"Set an alarm {when}{called}."
    secs = args.get("hours", 0) * 3600 + args.get("minutes", 0) * 60 + args.get("seconds", 0)
    if secs < 1:
        raise ToolError("How long should the timer run?")
    if secs > store.MAX_SECONDS:
        raise ToolError("Timers can run for up to 24 hours.")
    t = _create(store.clean_name(given, DEFAULT_TIMER), now, seconds=secs)
    if given:
        return f"Started {t['name']} for {_amount(secs)}."
    called = "" if t["name"] == DEFAULT_TIMER else f", called {t['name']}"
    return f"Started a timer for {_amount(secs)}{called}."


def _start_preset(args, now):
    presets = store.presets()
    if not presets:
        raise ToolError("There are no saved presets; save one on the Timers page.")
    key = _norm(args["preset"])
    p = next((p for p in presets if _norm(p["name"]) == key), None)
    if p is None:
        raise ToolError(f"There's no preset called {args['preset']!r}; the presets are "
                        f"{_join(_names(presets), 'and')}.")
    t = _create(p["name"], now, seconds=p["seconds"], speakers=p.get("speakers"))
    return f"Started {t['name']} for {_amount(p['seconds'])}."


def _finished(t):
    if t["state"] == store.RING:
        return ToolError(f"{t['name']} is ringing; stop it or add time instead.")
    return ToolError(f"{t['name']} has already finished.")


def _pause_timer(args, now):
    t = _pick(args.get("timer"), fits=lambda t: t["state"] == store.RUN)
    if t["state"] == store.PAUSE:
        return f"{t['name']} is already paused."
    if t["state"] != store.RUN:
        raise _finished(t)
    t = homepage.apply(t["id"], "pause", now=now)
    return f"Paused {t['name']} with {_amount(store.remaining(t, now))} left."


def _resume_timer(args, now):
    t = _pick(args.get("timer"), fits=lambda t: t["state"] == store.PAUSE)
    if t["state"] == store.RUN:
        return f"{t['name']} is already running."
    if t["state"] != store.PAUSE:
        raise _finished(t)
    t = homepage.apply(t["id"], "resume", now=now)
    return f"Resumed {t['name']}; it has {_amount(store.remaining(t, now))} left."


def _add_time(args, now):
    secs = args.get("minutes", 0) * 60 + args.get("seconds", 0)
    if not secs:
        raise ToolError("How much time should I add or take off?")
    t = _pick(args.get("timer"), fits=lambda t: t["state"] in (store.RUN, store.PAUSE))
    name, was = t["name"], t["state"]
    if was in (store.RUN, store.PAUSE) and store.remaining(t, now) + secs < 1:
        raise ToolError(f"{name} only has {_amount(store.remaining(t, now))} left.")
    if was not in (store.RUN, store.PAUSE) and secs < 0:
        raise _finished(t)
    try:
        t = store.adjust(t["id"], secs, now=now)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if was == store.RING:
        worker.silence(t["id"])
    worker.poke()
    left = _amount(store.remaining(t, now))
    if was not in (store.RUN, store.PAUSE):
        return f"{name} will ring again in {left}."
    change = f"Added {_amount(secs)} to" if secs > 0 else f"Took {_amount(-secs)} off"
    if t["state"] == store.RUN and t["at"]:
        return f"{change} {name}; it now rings {_when(t['end'], now)}, in {left}."
    return f"{change} {name}; it has {left} left."


def _cancel_timer(args, now):
    t = _pick(args.get("timer"))
    homepage.apply(t["id"], "remove", now=now)
    return f"Cancelled {t['name']}."


def _stop_alarm(args, now):
    wanted = args.get("timer")
    if wanted:
        t = _pick(wanted, fits=lambda t: t["state"] == store.RING)
        if t["state"] != store.RING:
            return f"{t['name']} isn't ringing."
        ringing = [t]
    else:
        ringing = [t for t in _timers() if t["state"] == store.RING]
        if not ringing:
            return "Nothing is ringing."
    for t in ringing:
        homepage.apply(t["id"], "silence", now=now)
    return f"Stopped {_join([t['name'] for t in ringing], 'and')}."


_RUNNERS = {
    LIST_TIMERS: _list_timers,
    START_TIMER: _start_timer,
    START_PRESET: _start_preset,
    PAUSE_TIMER: _pause_timer,
    RESUME_TIMER: _resume_timer,
    ADD_TIME: _add_time,
    CANCEL_TIMER: _cancel_timer,
    STOP_ALARM: _stop_alarm,
}


def run_tool(tool_id, args, mode=None):
    if tool_id not in _RUNNERS:
        raise ToolError(f"Unknown tool {tool_id!r}")
    return _RUNNERS[tool_id](args or {}, clock())
