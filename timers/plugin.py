from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.alerts import CHIMES
from bdh_sdk.plugins import CHOICE, INT, LIFECYCLE_ALWAYS, MULTI, NETWORK_NONE

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
    api_version=2,
    # Screen alerts when a timer runs out, and announcements on the chosen speakers.
    permissions=("alerts", "speakers", "media"),  # TEMP: tests the hub approval flow; drop in the next release
    # Everything goes through the broker; the page's icons load in the browser.
    network=NETWORK_NONE,
    # The ticker has to run to ring timers on time.
    lifecycle=LIFECYCLE_ALWAYS,
    memory_mb=96,
)
