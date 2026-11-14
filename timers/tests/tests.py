import datetime as dt
import os
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from bdh_sdk import ON, WARN, alerts
from bdh_sdk.storage import set_state_backend
from bdh_sdk.testing import assert_plugin_importable
from timers import homepage, store, worker

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


@patch("bdh_sdk.alerts.dismiss")
@patch("bdh_sdk.alerts.push")
class WorkerTests(Fresh, TestCase):
    def test_expire_pushes_alert_and_repeat_resounds(self, push, dismiss):
        t = store.create("Pasta", seconds=60, now=T0)
        with patch.dict(os.environ, {"BDH_TIMERS_CHIME": "kalimba"}):
            worker.handle(store.tick(T0 + 61))
            worker.handle(store.tick(T0 + 71.5))
        first, again = push.call_args_list[0].args[0], push.call_args_list[1].args[0]
        self.assertEqual((first.app, first.key, first.title), ("timers", f"timer:{t['id']}", "Pasta"))
        self.assertEqual((first.sound, first.speak, first.resound), ("kalimba", "Pasta has expired", False))
        self.assertEqual(first.embed_url, f"/timers/popup/{t['id']}/")
        self.assertTrue(again.resound)
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

    def test_should_start(self, push, dismiss):
        self.assertFalse(worker.should_start(["manage.py", "test"]))
        self.assertTrue(worker.should_start(["/usr/bin/daphne", "x"]))
        with patch.dict(os.environ, {"BDH_TIMERS_WORKER": "0"}):
            self.assertFalse(worker.should_start(["/usr/bin/daphne"]))


@patch("bdh_sdk.alerts.dismiss")
class HomepageTests(Fresh, TestCase):
    def test_status_rows_and_actions(self, dismiss):
        self.assertEqual(homepage.status().text, "No timers")
        a = store.create("Rice", seconds=900)
        store.create("Eggs", seconds=300)
        st = homepage.status()
        self.assertEqual(st.level, ON)
        self.assertTrue(st.text.startswith("Eggs "))
        self.assertEqual([r.title for r in st.rows], ["Rice", "Eggs"])
        self.assertEqual([x.id for x in st.rows[0].actions], ["pause", "remove"])
        self.assertEqual(homepage.run_action(a["id"], "pause"), "Paused Rice")
        self.assertEqual(store.get(a["id"])["state"], "pause")
        with self.assertRaises(LookupError):
            homepage.run_action(a["id"], "explode")

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


class PluginTests(SimpleTestCase):
    def test_plugin_importable(self):
        assert_plugin_importable("timers.plugin")
