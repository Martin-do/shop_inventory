from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("login/", RedirectView.as_view(url="/accounts/login/", permanent=False)),
    path("logout/", RedirectView.as_view(url="/accounts/logout/", permanent=False)),
    path("", include("inventory.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
