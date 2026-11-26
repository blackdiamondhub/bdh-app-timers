import datetime as dt
import os
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from bdh_sdk import ON, WARN, alerts
from bdh_sdk.storage import set_state_backend
from bdh_sdk.testing import assert_plugin_importable
from timers import assistant, homepage, store, worker

T0 = 1_800_000_000.0


class Fresh:
    def setUp(self):
        set_state_backend(None)
        store.reset_cache()
        self.addCleanup(store.reset_cache)
        # Never reach real speakers (or spawn announce threads) from tests.
        self.speakers_patch = patch("timers.worker.speakers", return_value=[])
        self.speakers_patch.start()
        self.addCleanup(patch.stopall)


class StoreTests(Fresh, TestCase):
    def test_create_and_names(self):
        t = store.create("  Pasta   water ", seconds=540, now=T0)
        self.assertEqual((t["name"], t["total"], t["state"], t["end"]), ("Pasta water", 540, "run", T0 + 540))
        self.assertEqual(store.create("", seconds=5, now=T0)["name"], "Timer")
        for bad in (0, -5, "x", None, store.MAX_SECONDS + 1):
            with self.assertRaises(ValueError):
                store.create("x", seconds=bad, now=T0)

    def test_limit(self):
        for n in range(store.MAX_TIMERS):
            store.create(f"t{n}", seconds=60, now=T0)
        with self.assertRaisesMessage(ValueError, "up to 12"):
            store.create("one more", seconds=60, now=T0)

    def test_ends_at_rolls_to_tomorrow(self):
        with patch.object(store, "_tz", return_value=dt.timezone.utc):
            now = dt.datetime(2026, 11, 14, 18, 0, tzinfo=dt.timezone.utc).timestamp()
            later = store.create("Taxi", at="18:30", now=now)
            self.assertEqual(later["total"], 1800)
            self.assertTrue(later["at"])
            tomorrow = store.create("Wake", at="07:00", now=now)
            self.assertEqual(tomorrow["total"], 13 * 3600)
            self.assertEqual(store.snapshot(later, now)["ends"], "6:30 PM")
            with self.assertRaises(ValueError):
                store.create("x", at="25:00", now=now)

    def test_pause_resume_add_reset(self):
        t = store.create("Eggs", seconds=300, now=T0)
        t = store.action(t["id"], "pause", now=T0 + 100)
        self.assertEqual((t["state"], t["left"]), ("pause", 200))
        self.assertEqual(store.remaining(t, T0 + 999), 200)
        t = store.action(t["id"], "add", now=T0 + 150)
        self.assertEqual((t["left"], t["total"]), (260, 360))
        t = store.action(t["id"], "resume", now=T0 + 200)
        self.assertEqual(t["end"], T0 + 460)
        t = store.action(t["id"], "reset", now=T0 + 210)
        self.assertEqual((t["state"], t["left"]), ("pause", 360))
        self.assertIsNone(store.action(t["id"], "remove"))
        self.assertEqual(store.timers(), [])
        with self.assertRaises(LookupError):
            store.action(t["id"], "pause")

    def test_tick_expire_repeat_done(self):
        t = store.create("Eggs", seconds=60, now=T0)
        self.assertEqual(store.tick(T0 + 59), [])
        self.assertAlmostEqual(store.next_wake(T0 + 50), 10)
        self.assertEqual([e for e, _ in store.tick(T0 + 60.2)], ["expire"])
        self.assertEqual(store.get(t["id"])["state"], "ring")
        self.assertEqual(store.tick(T0 + 65), [])
        self.assertEqual([e for e, _ in store.tick(T0 + 70.3)], ["repeat"])
        self.assertEqual(store.tick(T0 + 75), [])
        self.assertEqual([e for e, _ in store.tick(T0 + 90.3)], ["done"])
        self.assertEqual(store.get(t["id"])["state"], "done")
        self.assertIsNone(store.next_wake(T0 + 91))
        # a finished timer stays until closed, and can be snoozed or restarted
        t = store.action(t["id"], "add", now=T0 + 100)
        self.assertEqual((t["state"], t["end"]), ("run", T0 + 160))
        store.tick(T0 + 161)
        t = store.action(t["id"], "silence", now=T0 + 162)
        self.assertEqual(t["state"], "done")
        t = store.action(t["id"], "restart", now=T0 + 200)
        self.assertEqual((t["state"], t["end"]), ("run", T0 + 260))

    def test_long_overdue_after_restart_does_not_ring(self):
        t = store.create("Bread", seconds=60, now=T0)
        store.reset_cache()  # as if the hub restarted
        self.assertEqual(store.tick(T0 + 3600), [])
        self.assertEqual(store.get(t["id"])["state"], "done")

    def test_presets(self):
        p = store.add_preset("Egg timer", 420)
        t = store.start_preset(p["id"], now=T0)
        self.assertEqual((t["name"], t["total"]), ("Egg timer", 420))
        self.assertIsNone(t["speakers"])
        store.delete_preset(p["id"])
        with self.assertRaises(LookupError):
            store.start_preset(p["id"])
        for n in range(store.MAX_PRESETS):
            store.add_preset(f"p{n}", 60)
        with self.assertRaises(ValueError):
            store.add_preset("too many", 60)

    def test_fmt(self):
        self.assertEqual([store.fmt(s) for s in (0, 0.2, 59.5, 61, 3600, 3725)],
                         ["0:00", "0:01", "1:00", "1:01", "1:00:00", "1:02:05"])

    def test_speakers_cleaned_and_kept_by_presets(self):
        spk = ["media_player.kitchen", "media_player.kitchen", "light.oops", 5, "media_player." + "x" * 100]
        self.assertEqual(store.clean_speakers(spk), ["media_player.kitchen"])
        self.assertEqual(store.clean_speakers([]), [])
        with self.assertRaises(ValueError):
            store.create("x", seconds=5, speakers="media_player.kitchen")
        p = store.add_preset("Eggs", 420, speakers=["media_player.deck"])
        self.assertEqual(store.start_preset(p["id"], now=T0)["speakers"], ["media_player.deck"])


@patch("bdh_sdk.alerts.dismiss")
@patch("bdh_sdk.alerts.push")
class WorkerTests(Fresh, TestCase):
    def test_expire_pushes_alert_and_repeat_resounds(self, push, dismiss):
        t = store.create("Pasta", seconds=60, now=T0)
        with patch.dict(os.environ, {"BDH_TIMERS_CHIME": "kalimba"}):
            worker.handle(store.tick(T0 + 61), now=T0 + 61)
            worker.handle(store.tick(T0 + 71.5), now=T0 + 71.5)
        first, again = push.call_args_list[0].args[0], push.call_args_list[1].args[0]
        self.assertEqual((first.app, first.key, first.title), ("timers", f"timer:{t['id']}", "Pasta"))
        self.assertEqual((first.sound, first.speak, first.resound), ("kalimba", "Pasta has expired", False))
        self.assertEqual(first.embed_url, f"/timers/popup/{t['id']}/")
        self.assertTrue(again.resound)
        # The popup's countdown runs out when the ringing stops, repeat included.
        self.assertEqual((first.ttl, again.ttl), (store.RING_S, store.RING_S - 10))
        worker.handle(store.tick(T0 + 95))
        dismiss.assert_called_once_with("timers", f"timer:{t['id']}")

    def test_bad_chime_falls_back(self, push, dismiss):
        with patch.dict(os.environ, {"BDH_TIMERS_CHIME": "nope"}):
            self.assertEqual(worker.chime(), "soft-bell")
        with patch.dict(os.environ, {"BDH_TIMERS_CHIME": alerts.SILENT}):
            self.assertEqual(worker.chime(), alerts.SILENT)

    @patch("bdh_sdk.speakers.announce")
    def test_speakers_announced_in_background(self, announce, push, dismiss):
        env = {"BDH_TIMERS_SPEAKERS": "media_player.kitchen", "BDH_TIMERS_VOLUME": "40",
               "BDH_TIMERS_CHIME": "none"}
        self.speakers_patch.stop()
        with patch.dict(os.environ, env), patch("threading.Thread") as thread:
            worker.ring({"id": "abc", "name": "Eggs"})
            target, args = thread.call_args.kwargs["target"], thread.call_args.kwargs["args"]
            target(*args)
        announce.assert_called_once_with("Eggs has expired", ["media_player.kitchen"], volume=40,
                                         chime=None, voice=worker.VOICE)

    def test_timer_speakers_override_app_setting(self, push, dismiss):
        self.speakers_patch.stop()
        with patch.dict(os.environ, {"BDH_TIMERS_SPEAKERS": "media_player.kitchen"}), \
                patch("threading.Thread") as thread:
            worker.ring({"id": "a", "name": "Eggs", "speakers": ["media_player.deck"]})
            self.assertEqual(thread.call_args.kwargs["args"][1], ["media_player.deck"])
            thread.reset_mock()
            worker.ring({"id": "b", "name": "Eggs", "speakers": []})
            thread.assert_not_called()
            worker.ring({"id": "c", "name": "Eggs"})
            self.assertEqual(thread.call_args.kwargs["args"][1], ["media_player.kitchen"])

    def test_unset_speakers_setting_means_screens_only(self, push, dismiss):
        self.speakers_patch.stop()
        with patch("timers.worker.app_setting", return_value=None), patch("threading.Thread") as thread:
            self.assertEqual(worker.speakers(), [])
            worker.ring({"id": "a", "name": "Eggs"})
            thread.assert_not_called()

    def test_should_start(self, push, dismiss):
        self.assertFalse(worker.should_start(["manage.py", "test"]))
        self.assertTrue(worker.should_start(["/usr/bin/daphne", "x"]))
        self.assertTrue(worker.should_start(["/usr/local/lib/python3.13/site-packages/bdh_sdk/runtime/__main__.py"]))
        self.assertFalse(worker.should_start(["/x/__main__.py", "test"]))
        with patch.dict(os.environ, {"BDH_TIMERS_WORKER": "0"}):
            self.assertFalse(worker.should_start(["/usr/bin/daphne"]))


class ManifestTests(SimpleTestCase):
    def test_hub_api_2_manifest(self):
        from timers.plugin import spec
        m = spec.manifest()
        self.assertEqual(m["api_version"], 2)
        self.assertEqual(m["permissions"], ["alerts", "speakers"])
        self.assertEqual((m["network"], m["lifecycle"], m["memory_mb"]), ("none", "always", 96))


class BrokerTests(TestCase):
    """In its container the app keeps timers in the hub's state store and rings through the broker."""

    def setUp(self):
        from bdh_sdk import rpc
        from bdh_sdk.testing import FakeBroker

        store.reset_cache()
        self.addCleanup(store.reset_cache)
        self.broker = FakeBroker(grants=("settings.get", "state.*", "alerts.*", "speakers.*"),
                                 install=True, app_id="timers")
        self.broker.start()
        self.addCleanup(self.broker.stop)
        self.flush = lambda: rpc.flush(5)
        self.broker.settings.update({("timers", "chime"): "kalimba",
                                     ("timers", "speakers"): ["media_player.kitchen"],
                                     ("timers", "volume"): 30})
        # Patch only the worker's `threading` reference: patching `threading.Thread` itself would
        # also hijack the SDK's rpc sender thread and the announcement would never reach the broker.
        threading = patch("timers.worker.threading")
        self.addCleanup(threading.stop)
        threading.start().Thread.side_effect = lambda target, args=(), **kw: type(
            "T", (), {"start": lambda self: target(*args)})()

    def test_timer_rings_and_announces_via_broker(self):
        t = store.create("Eggs", seconds=60, now=T0)
        self.assertTrue(any(app == "timers" for app, _ in self.broker.state))
        worker.handle(store.tick(T0 + 61))
        self.flush()
        (alert,) = self.broker.ops("alerts.push")
        self.assertEqual((alert["key"], alert["embed_url"], alert["sound"]),
                         (f"timer:{t['id']}", f"/timers/popup/{t['id']}/", "kalimba"))
        (spoken,) = self.broker.ops("speakers.announce")
        self.assertEqual((spoken["message"], spoken["speakers"], spoken["volume"]),
                         ("Eggs has expired", ["media_player.kitchen"], 30))

    def test_speakers_refused_still_rings_screens(self):
        self.broker.grants = ["settings.get", "state.*", "alerts.*"]
        store.create("Eggs", seconds=60, now=T0)
        with self.assertLogs("timers.worker", "ERROR"):
            worker.handle(store.tick(T0 + 61))
        self.flush()
        self.assertEqual(len(self.broker.ops("alerts.push")), 1)


@patch("bdh_sdk.alerts.dismiss")
class HomepageTests(Fresh, TestCase):
    def test_status_rows_and_actions(self, dismiss):
        self.assertEqual(homepage.status().text, "No timers")
        a = store.create("Rice", seconds=900)
        store.create("Eggs", seconds=300)
        st = homepage.status()
        self.assertEqual(st.level, ON)
        self.assertTrue(st.text.startswith("Eggs "))
        self.assertEqual([r.title for r in st.rows], ["Eggs", "Rice"])
        self.assertEqual([x.id for x in st.rows[0].actions], ["pause", "remove"])
        self.assertEqual(homepage.run_action(a["id"], "pause"), "Paused Rice")
        self.assertEqual(store.get(a["id"])["state"], "pause")
        with self.assertRaises(LookupError):
            homepage.run_action(a["id"], "explode")

    def test_status_counts_down_on_screen(self, dismiss):
        with patch("time.time", return_value=T0):
            t = store.create("Eggs", seconds=300)
            r = store.create("Rice", seconds=900)
            store.action(r["id"], "pause")
            st = homepage.status()
        self.assertEqual((st.text, st.ends_at), ("Eggs {left}", t["end"]))
        eggs, rice = sorted(st.rows, key=lambda r: r.title)
        self.assertEqual((eggs.detail, eggs.ends_at), ("{left}", t["end"]))
        self.assertEqual((rice.detail, rice.ends_at), ("15:00 · paused", None))

    def test_rows_put_finished_timers_last(self, dismiss):
        eggs = store.create("Eggs", seconds=1, now=T0)
        store.tick(T0 + 2)
        store.action(eggs["id"], "silence", now=T0 + 2)
        store.create("Chime", seconds=60, now=T0 + 3)
        paused = store.create("Rice", seconds=900, now=T0 + 3)
        store.action(paused["id"], "pause", now=T0 + 3)
        pizza = store.create("Pizza", seconds=1, now=T0 + 3)
        store.tick(T0 + 5)
        with patch("time.time", return_value=T0 + 5):
            st = homepage.status()
        self.assertEqual([r.title for r in st.rows], ["Pizza", "Chime", "Rice", "Eggs"])
        self.assertEqual(store.get(pizza["id"])["state"], "ring")

    def test_ringing_is_a_warning_and_silence_dismisses(self, dismiss):
        t = store.create("Pizza", seconds=1, now=T0)
        store.tick(T0 + 2)
        with patch("time.time", return_value=T0 + 3):
            st = homepage.status()
        self.assertEqual((st.level, st.text), (WARN, "Pizza: time's up"))
        homepage.run_action(t["id"], "silence")
        dismiss.assert_called_once_with("timers", f"timer:{t['id']}")

    def test_presets_are_shortcuts(self, dismiss):
        p = store.add_preset("Egg timer", 420)
        sc = homepage.shortcuts()
        self.assertEqual([(s.id, s.title) for s in sc], [(p["id"], "Egg timer")])
        self.assertEqual(homepage.run_shortcut(p["id"]), "Started Egg timer (7:00)")
        self.assertEqual(len(store.timers()), 1)


@patch("bdh_sdk.alerts.dismiss")
class ViewTests(Fresh, TestCase):
    def post(self, name, body=None, *args):
        return self.client.post(reverse(name, args=args), body or {}, content_type="application/json")

    def test_page_and_state(self, dismiss):
        self.assertEqual(self.client.get(reverse("timers_index")).status_code, 200)
        r = self.post("timers_create", {"name": "Tea", "seconds": 180, "save": True})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual([t["name"] for t in data["timers"]], ["Tea"])
        self.assertEqual([p["name"] for p in data["presets"]], ["Tea"])
        self.assertEqual(self.client.get(reverse("timers_state")).json()["max"], store.MAX_TIMERS)
        page = self.client.get(reverse("timers_index"))
        self.assertContains(page, "tm-initial")

    def test_create_errors(self, dismiss):
        r = self.post("timers_create", {"name": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("duration", r.json()["error"])
        self.assertEqual(self.post("timers_create", {"at": "99:99"}).status_code, 400)

    def test_alarm_is_not_saved_as_preset(self, dismiss):
        self.post("timers_create", {"name": "Taxi", "at": "18:30", "save": True})
        self.assertEqual(store.presets(), [])

    def test_create_with_speakers_saves_them_on_preset(self, dismiss):
        self.post("timers_create", {"name": "Tea", "seconds": 60, "save": True, "speakers": ["media_player.deck"]})
        self.assertEqual(store.timers()[0]["speakers"], ["media_player.deck"])
        self.assertEqual(store.presets()[0]["speakers"], ["media_player.deck"])
        self.post("timers_create", {"name": "Rice", "seconds": 60})
        self.assertIsNone(store.timers()[1]["speakers"])

    def test_speaker_list(self, dismiss):
        with patch("bdh_sdk.speakers.list_speakers", return_value=[("media_player.deck", "Deck (Sonos)")]):
            data = self.client.get(reverse("timers_speakers")).json()
        self.assertEqual(data, {"speakers": [["media_player.deck", "Deck (Sonos)"]], "default": []})
        with patch("bdh_sdk.speakers.list_speakers", side_effect=RuntimeError("HA down")), \
                self.assertLogs("timers.views", "ERROR"):
            self.assertEqual(self.client.get(reverse("timers_speakers")).json()["speakers"], [])

    def test_controls_and_popup(self, dismiss):
        t = store.create("Soup", seconds=60)
        self.assertEqual(self.post("timers_control", None, t["id"], "pause").json()["timers"][0]["state"], "pause")
        self.assertEqual(self.post("timers_control", None, t["id"], "explode").status_code, 404)
        self.assertEqual(self.post("timers_control", None, "nope", "pause").status_code, 404)
        r = self.client.get(reverse("timers_popup", args=[t["id"]]))
        self.assertContains(r, "Soup")
        self.assertEqual(self.client.get(reverse("timers_popup", args=["nope"])).status_code, 404)
        self.post("timers_control", None, t["id"], "remove")
        self.assertEqual(store.timers(), [])

    def test_presets_api(self, dismiss):
        p = self.post("timers_preset_create", {"name": "Eggs", "seconds": 420}).json()["presets"][0]
        self.assertEqual(len(self.post("timers_preset_control", None, p["id"], "start").json()["timers"]), 1)
        self.assertEqual(self.post("timers_preset_control", None, p["id"], "delete").json()["presets"], [])
        self.assertEqual(self.post("timers_preset_control", None, p["id"], "delete").status_code, 404)
        self.assertEqual(self.post("timers_preset_control", None, "x", "nuke").status_code, 404)


EVENING = dt.datetime(2026, 11, 26, 18, 0, tzinfo=dt.timezone.utc).timestamp()


class AssistantTests(TestCase):
    """The assistant tools, called the way the hub calls them (FakeBroker sessions)."""

    def setUp(self):
        from bdh_sdk import Stay
        from bdh_sdk.testing import FakeBroker

        store.reset_cache()
        self.addCleanup(store.reset_cache)
        self.broker = FakeBroker(install=True, app_id="timers").start()
        self.addCleanup(self.broker.stop)
        self.broker.stay = Stay(guest="Smith")  # a voice session during a stay is in guest mode
        self.now = EVENING
        patch.object(assistant, "clock", lambda: self.now).start()
        patch.object(store, "_tz", return_value=dt.timezone.utc).start()
        self.dismiss = patch("bdh_sdk.alerts.dismiss").start()
        self.addCleanup(patch.stopall)

    def session(self, mode="guest"):
        from bdh_sdk import tools as sdk_tools
        self.broker.assistant_tools.clear()
        for tool in assistant.tools(mode=mode):
            self.broker.add_tool(tool, lambda args, t=tool.id: assistant.run_tool(t, args, mode=mode))
        return sdk_tools.open_session()

    def call(self, name, args=None):
        from bdh_sdk import tools as sdk_tools
        return sdk_tools.call_tool(self.session(), name, args)

    def ok(self, name, args=None):
        answer = self.call(name, args)
        self.assertTrue(answer["ok"], answer)
        return answer["result"]

    def error(self, name, args=None):
        answer = self.call(name, args)
        self.assertFalse(answer["ok"], answer)
        return answer["error"]

    def by_name(self, name):
        return next(t for t in store.timers() if t["name"] == name)

    def ring(self, name, seconds=1):
        t = store.create(name, seconds=seconds, now=self.now)
        store.tick(self.now + seconds + 0.1)
        return t

    def test_every_mode_gets_every_tool(self):
        from bdh_sdk import ALL_MODES
        ids = ["list_timers", "start_timer", "start_preset", "pause_timer", "resume_timer", "add_time",
               "cancel_timer", "stop_alarm"]
        store.add_preset("Egg timer", 420)
        for mode in sorted(ALL_MODES):
            with self.subTest(mode=mode):
                tools = assistant.tools(mode=mode)
                self.assertEqual([t.id for t in tools], ids)
                self.assertTrue(all(t.modes == ALL_MODES for t in tools))
                self.assertEqual([t.id for t in tools if t.readonly], ["list_timers"])
                # Timers come and go faster than the tool list is cached: names are free text.
                for t in tools:
                    self.assertNotIn("enum", t.params["properties"].get("timer", {}))
                preset = next(t for t in tools if t.id == "start_preset").params["properties"]["preset"]
                self.assertEqual(preset["enum"], ["Egg timer"])
        for p in store.presets():
            store.delete_preset(p["id"])
        self.assertNotIn("enum", assistant.tools()[2].params["properties"]["preset"])

    def test_start_and_list(self):
        self.assertEqual(self.ok("list_timers"), "There are no timers.")
        self.assertEqual(self.ok("start_timer", {"name": "Pasta", "minutes": 9}), "Started Pasta for 9 minutes.")
        self.assertEqual(self.ok("start_timer", {"name": "Taxi", "ends_at": "18:30"}),
                         "Taxi will ring at 6:30 PM, in 30 minutes.")
        rice = store.create("Rice", seconds=900, now=self.now)
        store.action(rice["id"], "pause", now=self.now)
        self.ring("Eggs")
        store.add_preset("Egg timer", 420)
        self.now += 30
        self.assertEqual(self.ok("list_timers"),
                         "Eggs is ringing. Pasta has 8 minutes 30 seconds left. "
                         "Taxi rings at 6:30 PM, in 29 minutes 30 seconds. Rice is paused with 15 minutes left. "
                         "Saved presets: Egg timer, 7 minutes.")
        self.assertEqual(self.ok("start_timer", {"name": "Bread", "hours": 1, "minutes": 5, "seconds": 20}),
                         "Started Bread for 1 hour 5 minutes.")
        self.assertEqual(self.by_name("Bread")["total"], 3920)

    def test_default_names(self):
        self.assertEqual(self.ok("start_timer", {"seconds": 45}), "Started a timer for 45 seconds.")
        self.assertEqual(self.ok("start_timer", {"minutes": 2}), "Started a timer for 2 minutes, called Timer 2.")
        self.assertEqual(self.ok("start_timer", {"ends_at": "7:00"}),
                         "Set an alarm tomorrow at 7:00 AM, in 13 hours.")
        self.assertEqual([t["name"] for t in store.timers()], ["Timer", "Timer 2", "Alarm"])

    def test_starting_a_name_again(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        # A running timer keeps its name; the new one is numbered so each can be named.
        self.assertEqual(self.ok("start_timer", {"name": "pasta", "minutes": 3}), "Started pasta 2 for 3 minutes.")
        # A finished one is just cleared away.
        self.ring("Eggs")
        store.action(self.by_name("Eggs")["id"], "silence", now=self.now)
        self.assertEqual(self.ok("start_timer", {"name": "Eggs", "minutes": 7}), "Started Eggs for 7 minutes.")
        self.assertEqual([(t["name"], t["state"]) for t in store.timers()],
                         [("Pasta", "run"), ("pasta 2", "run"), ("Eggs", "run")])

    def test_start_validation(self):
        self.assertEqual(self.error("start_timer", {"name": "Pasta"}), "How long should the timer run?")
        self.assertEqual(self.error("start_timer", {"minutes": 0}), "How long should the timer run?")
        self.assertEqual(self.error("start_timer", {"minutes": 5, "ends_at": "18:30"}),
                         "Give either how long the timer runs or when it rings, not both.")
        self.assertEqual(self.error("start_timer", {"hours": 24, "minutes": 1}), "Timers can run for up to 24 hours.")
        self.assertEqual(self.error("start_timer", {"ends_at": "25:00"}),
                         "'25:00' isn't a time: give ends_at as 24-hour HH:MM, like 18:30.")
        for args in ({"hours": 25}, {"minutes": -5}, {"minutes": "five"}, {"name": "x" * 41, "minutes": 1},
                     {"minutes": 1, "colour": "red"}):
            with self.subTest(args=args):
                self.assertIn("invalid arguments", self.error("start_timer", args))
        self.assertEqual(store.timers(), [])
        for n in range(store.MAX_TIMERS):
            store.create(f"t{n}", seconds=60, now=self.now)
        self.assertEqual(self.error("start_timer", {"minutes": 1}),
                         "There are already 12 timers, the most there can be. Cancel one first.")

    def test_start_preset(self):
        self.assertEqual(self.error("start_preset", {"preset": "Egg timer"}),
                         "There are no saved presets; save one on the Timers page.")
        store.add_preset("Egg timer", 420, speakers=["media_player.deck"])
        store.add_preset("Tea", 180)
        self.assertEqual(self.ok("start_preset", {"preset": "Egg timer"}), "Started Egg timer for 7 minutes.")
        self.assertEqual(self.by_name("Egg timer")["speakers"], ["media_player.deck"])
        self.assertIn("invalid arguments", self.error("start_preset", {"preset": "Soup"}))
        with self.assertRaisesRegex(assistant.ToolError, "no preset called 'Soup'; the presets are Egg timer and Tea"):
            assistant.run_tool("start_preset", {"preset": "Soup"})
        self.assertEqual(assistant.run_tool("start_preset", {"preset": "the tea timer"}), "Started Tea for 3 minutes.")

    def test_pause_and_resume(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        self.ok("start_timer", {"name": "Rice", "minutes": 15})
        self.now += 60
        self.assertEqual(self.error("pause_timer"), "Which timer: Pasta or Rice?")
        self.assertEqual(self.ok("pause_timer", {"timer": "pasta"}), "Paused Pasta with 8 minutes left.")
        self.assertEqual(self.by_name("Pasta")["state"], "pause")
        self.assertEqual(self.ok("pause_timer", {"timer": "Pasta"}), "Pasta is already paused.")
        self.now += 600
        # Only one timer is paused, so "resume the timer" means it.
        self.assertEqual(self.ok("resume_timer"), "Resumed Pasta; it has 8 minutes left.")
        self.assertEqual(self.by_name("Pasta")["end"], self.now + 480)
        self.assertEqual(self.ok("resume_timer", {"timer": "Rice"}), "Rice is already running.")
        self.ring("Eggs")
        self.assertEqual(self.error("pause_timer", {"timer": "Eggs"}), "Eggs is ringing; stop it or add time instead.")

    def test_one_timer_needs_no_name(self):
        self.assertEqual(self.error("pause_timer"), "There are no timers.")
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        self.assertEqual(self.ok("pause_timer"), "Paused Pasta with 9 minutes left.")
        self.assertEqual(self.ok("cancel_timer"), "Cancelled Pasta.")

    def test_names_match_loosely(self):
        store.create("Pasta Timer", seconds=540, now=self.now)
        store.create("Taxi", seconds=1800, now=self.now)
        self.assertEqual(self.ok("pause_timer", {"timer": "pasta"}), "Paused Pasta Timer with 9 minutes left.")
        self.assertEqual(self.ok("resume_timer", {"timer": "the  Pasta timer"}),
                         "Resumed Pasta Timer; it has 9 minutes left.")
        self.assertEqual(self.ok("pause_timer", {"timer": "the taxi alarm"}), "Paused Taxi with 30 minutes left.")
        self.assertEqual(self.ok("cancel_timer", {"timer": "Taxi Timer"}), "Cancelled Taxi.")
        store.create("Timer", seconds=60, now=self.now)
        self.assertEqual(self.ok("cancel_timer", {"timer": "the timer"}), "Cancelled Timer.")

    def test_unknown_and_ambiguous_names(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        self.ok("start_timer", {"name": "Rice", "minutes": 15})
        self.ok("start_timer", {"name": "Taxi", "ends_at": "18:30"})
        self.assertEqual(self.error("cancel_timer", {"timer": "Soup"}),
                         "There's no timer called 'Soup'; the timers are Pasta, Rice and Taxi.")
        self.assertEqual(self.error("cancel_timer"), "Which timer: Pasta, Rice or Taxi?")
        self.assertEqual(self.error("add_time", {"minutes": 1}), "Which timer: Pasta, Rice or Taxi?")
        # Two timers with one name (made on the page) can't be told apart by voice.
        store.create("Rice", seconds=60, now=self.now)
        self.assertEqual(self.error("cancel_timer", {"timer": "rice"}),
                         "There are 2 timers called Rice; pick one on the Timers page.")
        self.assertEqual(len(store.timers()), 4)

    def test_add_time(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        self.assertEqual(self.ok("add_time", {"minutes": 2}), "Added 2 minutes to Pasta; it has 11 minutes left.")
        self.assertEqual(self.ok("add_time", {"seconds": -90}),
                         "Took 1 minute 30 seconds off Pasta; it has 9 minutes 30 seconds left.")
        self.assertEqual(self.by_name("Pasta")["end"], self.now + 570)
        self.assertEqual(self.error("add_time", {"minutes": -10}), "Pasta only has 9 minutes 30 seconds left.")
        self.assertEqual(self.error("add_time", {"minutes": 0}), "How much time should I add or take off?")
        self.assertEqual(self.error("add_time", {"minutes": 1440}), "Timers can run for up to 24 hours.")
        self.assertIn("invalid arguments", self.error("add_time", {"minutes": 1441}))
        self.ok("start_timer", {"name": "Taxi", "ends_at": "18:30"})
        self.assertEqual(self.ok("add_time", {"timer": "taxi", "minutes": 5}),
                         "Added 5 minutes to Taxi; it now rings at 6:35 PM, in 35 minutes.")
        pasta = self.by_name("Pasta")
        store.action(pasta["id"], "pause", now=self.now)
        self.assertEqual(self.ok("add_time", {"timer": "Pasta", "minutes": 1}),
                         "Added 1 minute to Pasta; it has 10 minutes 30 seconds left.")
        self.assertEqual(store.get(pasta["id"])["state"], "pause")

    def test_add_time_snoozes_a_ringing_timer(self):
        eggs = self.ring("Eggs")
        self.assertEqual(self.error("add_time", {"minutes": -1}), "Eggs is ringing; stop it or add time instead.")
        self.assertEqual(self.ok("add_time", {"minutes": 2}), "Eggs will ring again in 2 minutes.")
        t = store.get(eggs["id"])
        self.assertEqual((t["state"], t["end"]), ("run", self.now + 120))
        self.dismiss.assert_called_once_with("timers", f"timer:{eggs['id']}")

    def test_cancel_timer(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        eggs = self.ring("Eggs")
        self.assertEqual(self.ok("cancel_timer", {"timer": "eggs"}), "Cancelled Eggs.")
        self.dismiss.assert_called_once_with("timers", f"timer:{eggs['id']}")
        self.assertEqual(self.ok("cancel_timer", {"timer": "Pasta"}), "Cancelled Pasta.")
        self.assertEqual(store.timers(), [])

    def test_stop_alarm(self):
        self.ok("start_timer", {"name": "Pasta", "minutes": 9})
        self.assertEqual(self.ok("stop_alarm"), "Nothing is ringing.")
        self.assertEqual(self.ok("stop_alarm", {"timer": "Pasta"}), "Pasta isn't ringing.")
        eggs, pizza = self.ring("Eggs"), self.ring("Pizza")
        self.assertEqual(self.ok("stop_alarm", {"timer": "the eggs timer"}), "Stopped Eggs.")
        self.assertEqual(self.ok("stop_alarm"), "Stopped Pizza.")
        self.assertEqual([(t["name"], t["state"]) for t in store.timers()],
                         [("Pasta", "run"), ("Eggs", "done"), ("Pizza", "done")])
        self.assertEqual([c.args for c in self.dismiss.call_args_list],
                         [("timers", f"timer:{eggs['id']}"), ("timers", f"timer:{pizza['id']}")])
        self.ring("Rice")
        self.ring("Soup")
        self.assertEqual(self.ok("stop_alarm"), "Stopped Rice and Soup.")

    def test_hooks_serialise(self):
        import json

        from bdh_sdk import hooks
        from timers.plugin import spec
        listed = hooks.run(spec, "tools", mode="guest")
        self.assertEqual(len(listed), 8)
        json.dumps(listed)
        self.assertEqual(hooks.run(spec, "run_tool", tool_id="list_timers", args={}, mode="guest"),
                         "There are no timers.")
        with self.assertRaises(assistant.ToolError):
            hooks.run(spec, "run_tool", tool_id="nope", args={}, mode="guest")


class PluginTests(SimpleTestCase):
    def test_plugin_importable(self):
        assert_plugin_importable("timers.plugin")


class HubFrameLayoutTests(SimpleTestCase):
    """The app renders inside the hub's iframe, already below the hub navbar, so its
    CSS must not reserve space for that navbar (top:62px / 100vh-62px / phone 118px)
    nor scope rules under the hub's `.navbar`, which doesn't exist in the iframe."""

    def test_no_hub_navbar_offsets(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        self.assertTrue((root / "templates").is_dir(), root)
        self.assertTrue((root / "static").is_dir(), root)
        pattern = re.compile(r"top\s*:\s*(6[0-9]|118)px|100vh\s*-\s*(6[0-9]|118)px|\.navbar(?![-\w])")
        hits = []
        for path in list(root.glob("templates/**/*.html")) + list(root.glob("static/**/*.css")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
        self.assertEqual(hits, [])
