import json
import time

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from . import homepage, store, worker

CONTROLS = ("pause", "resume", "add", "reset", "restart", "silence", "remove")


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {}


def _error(msg, status=400):
    return JsonResponse({"error": str(msg)}, status=status)


def _state():
    now = time.time()
    return {"now": now, "max": store.MAX_TIMERS,
            "timers": [store.snapshot(t, now) for t in store.timers()],
            "presets": store.presets()}


def index(request):
    return render(request, "timers/index.html", {"state": _state()})


@xframe_options_sameorigin
def popup(request, timer_id):
    try:
        t = store.get(timer_id)
    except LookupError:
        raise Http404("no such timer") from None
    return render(request, "timers/popup.html", {"timer": store.snapshot(t, time.time())})


@require_GET
def state(request):
    return JsonResponse(_state())


@require_POST
def create(request):
    data = _body(request)
    try:
        t = store.create(data.get("name"), seconds=data.get("seconds"), at=data.get("at") or None)
        if data.get("save") and not data.get("at"):
            store.add_preset(t["name"], t["total"])
    except ValueError as exc:
        return _error(exc)
    worker.poke()
    return JsonResponse(_state())


@require_POST
def control(request, timer_id, act):
    if act not in CONTROLS:
        raise Http404("unknown action")
    try:
        homepage.apply(timer_id, act)
    except LookupError:
        raise Http404("no such timer") from None
    return JsonResponse(_state())


@require_POST
def preset_create(request):
    data = _body(request)
    try:
        store.add_preset(data.get("name"), data.get("seconds"))
    except ValueError as exc:
        return _error(exc)
    return JsonResponse(_state())


@require_POST
def preset_control(request, preset_id, act):
    try:
        if act == "start":
            store.start_preset(preset_id)
            worker.poke()
        elif act == "delete":
            store.delete_preset(preset_id)
        else:
            raise Http404("unknown action")
    except LookupError:
        raise Http404("no such preset") from None
    except ValueError as exc:
        return _error(exc)
    return JsonResponse(_state())
