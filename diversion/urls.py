from django.urls import path

from . import views


urlpatterns = [
    path("", views.status_view, name="status"),
    path("enable/", views.enable_view, name="enable"),
    path("set/", views.set_view, name="set"),
    path("disable/", views.disable_view, name="disable"),
    path("refresh/", views.refresh_view, name="refresh"),
    path("health/", views.health_view, name="health"),
]
