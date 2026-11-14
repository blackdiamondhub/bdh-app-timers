# bdh-app-timers

A [Black Diamond Hub](https://github.com/blackdiamondlodge/blackdiamondhub) app for named
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

## Notes

- Timers live in the app's state store and survive a hub restart. A timer that ran out while
  the hub was down is shown as done without ringing.
- A background thread (started in the server process only) ticks the timers. Set
  `BDH_TIMERS_WORKER=0` to disable it.
- Display performance: the dials repaint once a second; nothing animates continuously.
