# bdh-app-timers

A [Black Diamond Hub](https://github.com/blackdiamondhub/blackdiamondhub) app for named
kitchen timers and alarms.

- Up to 12 timers at once, each with a name, drawn as large analog dials.
- **Duration** timers ("Pasta, 9 minutes") or **Ends at** alarms ("Taxi at 6:30 PM", still
  showing the countdown).
- Pause, resume, +1 minute, reset and restart. Timers stay on screen until closed with the X.
- Saved presets (e.g. "Egg timer") on the Timers page, as homepage quick actions, and in the
  tile's status rows.
- When a timer runs out every screen shows a pop-up with the dial, plays the chime and says
  "*name* has expired" (Home Assistant TTS). It plays once more ~10 s later, then stops on its
  own after 30 s. **Stop** in the pop-up (or on any screen) silences it everywhere.
- Optionally also announces on Sonos/Alexa speakers.

## Settings

| Setting | Default | |
|---|---|---|
| Chime | Soft bell | Played on the screens (and Sonos) when a timer runs out. |
| Also announce on | none | Sonos and Alexa speakers. Empty = screens only. |
| Speaker volume | 50 % | Sonos only. |

Screens stay silent during the hub's night window; speaker announcements still play.

## Container (hub API 2)

Since 2.0.0 the app runs in its own container: permissions `alerts` (the pop-up on every screen)
and `speakers` (announcements), `network: none` (everything goes through the broker),
`lifecycle: always` (the ticker must run to ring timers on time), `memory_mb: 96`. Timers and
presets stay in the hub's state store, so moving between in-process and container needs no
migration. Without the `speakers` grant timers still ring on the screens.

In the container, settings come from the hub through the broker; `BDH_TIMERS_*` environment
variables on the hub are not visible to the app.

CI's `image` job (from bdh-sdk's `app-ci.yml`) locks the dependencies against the hub's app
runtime, builds the app's image and smoke-tests it the way the supervisor runs it.

## Assistant tools

The hub's assistant (voice/chat, bdh-sdk 0.46+ `tools` / `run_tool`) can use the timers the way
the page can. Every tool is offered in every mode (guest, dashboard and owner), since guests use
the timers too. Tools that change a timer take an optional `timer` name: free text, not an enum,
because timers come and go faster than the hub caches the tool list. Names match loosely on both
sides (case, extra spaces, a leading "the" and a trailing "timer"/"alarm", so "pasta" finds
"Pasta Timer" and "the pasta timer" finds "Pasta"). Without a name the tool uses the only timer,
or the only one it applies to (the one paused timer for `resume_timer`); otherwise it answers
"Which timer: Pasta or Rice?", and an unknown name lists the timers.

| Tool | Read-only | Params | Does |
| --- | --- | --- | --- |
| `list_timers` | yes | none | "Eggs is ringing. Pasta has 4 minutes 30 seconds left. Taxi rings at 6:30 PM, in 25 minutes. Rice is paused with 15 minutes left. Saved presets: Egg timer, 7 minutes." |
| `start_timer` | no | `name`?, `hours` (0-24), `minutes` (0-1440), `seconds`, or `ends_at` (local 24 h `HH:MM`) | "Started Pasta for 9 minutes." / "Taxi will ring at 6:30 PM, in 25 minutes." |
| `start_preset` | no | `preset` (enum of the saved presets) | Starts a saved preset with its speakers. |
| `pause_timer` | no | `timer`? | "Paused Pasta with 8 minutes left." |
| `resume_timer` | no | `timer`? | "Resumed Pasta; it has 8 minutes left." |
| `add_time` | no | `minutes`, `seconds` (negative takes time off), `timer`? | Adds or takes off time; on a ringing or finished timer, silences it and snoozes for that long. |
| `cancel_timer` | no | `timer`? | Closes the timer, like the X (and silences it if it's ringing). |
| `stop_alarm` | no | `timer`? | Silences a ringing timer on every screen, like Stop; without a name, every ringing timer. |

A timer without a name is called "Timer" (an alarm "Alarm"). Starting a name that a running timer
already has numbers the new one ("Pasta 2") so each can still be told apart by voice; a finished
timer of that name is closed instead. "Ends at" times use the hub's time zone, the same as the
page. Failures (no such timer, too many timers, over 24 hours, more time off than is left) come
back as short messages the assistant can pass on.

## Notes

- Timers live in the app's state store and survive a hub restart. A timer that ran out while
  the hub was down is shown as done without ringing.
- A background thread (started in the server process only) ticks the timers. When running
  in-process, set `BDH_TIMERS_WORKER=0` on the hub to disable it.
- Display performance: the dials repaint once a second; nothing animates continuously.
