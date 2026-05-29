from django.conf import settings
from django.urls import include, path


urlpatterns = [
    path("services/", include("diversion.urls")),
]

if settings.PHONE_SERVICES_ENABLE_ROOT_MOUNT:
    urlpatterns.append(path("", include("diversion.urls")))
