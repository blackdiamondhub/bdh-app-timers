from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.alerts import CHIMES
from bdh_sdk.plugins import CHOICE, INT, MULTI

CHIME_CHOICES = (("none", "No chime"),) + tuple(
    (c, c.replace("-", " ").capitalize()) for c in CHIMES)

spec = PluginSpec(
    id="timers",
    app="timers",
    urls="timers.urls",
    tiles=(
        Tile("Timers", "timers_index", "timers/hub/icon.svg", section="lodge",
             subtitle="Kitchen timers and alarms", order=60),
    ),
    status="timers.homepage.status",
    run_action="timers.homepage.run_action",
    shortcuts="timers.homepage.shortcuts",
    run_shortcut="timers.homepage.run_shortcut",
    settings=(
        Setting("chime", "Chime", type=CHOICE, default="soft-bell", choices=CHIME_CHOICES,
                help="Played on the screens (and Sonos) when a timer runs out."),
        Setting("speakers", "Also announce on", type=MULTI, default=None,
                choices_from="bdh_sdk.speakers.list_speakers",
                help="Sonos and Alexa speakers. Leave empty to use only the screens."),
        Setting("volume", "Speaker volume", type=INT, default=50, minimum=0, maximum=100, unit="%",
                help="Sonos only. Speakers return to their own volume afterwards."),
    ),
)
