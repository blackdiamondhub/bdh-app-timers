from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="timers_index"),
    path("popup/<str:timer_id>/", views.popup, name="timers_popup"),
    path("api/state/", views.state, name="timers_state"),
    path("api/speakers/", views.speaker_list, name="timers_speakers"),
    path("api/timers/", views.create, name="timers_create"),
    path("api/timers/<str:timer_id>/<str:act>/", views.control, name="timers_control"),
    path("api/presets/", views.preset_create, name="timers_preset_create"),
    path("api/presets/<str:preset_id>/<str:act>/", views.preset_control, name="timers_preset_control"),
]
