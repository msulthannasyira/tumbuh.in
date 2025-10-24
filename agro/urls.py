from django.urls import path

from . import views

app_name = "agro"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("auth/login/", views.TumbuhLoginView.as_view(), name="login"),
    path("auth/logout/", views.TumbuhLogoutView.as_view(), name="logout"),
    path("auth/register/", views.register, name="register"),
    path("app/", views.dashboard, name="dashboard"),
    path("areas/process/", views.process_area, name="process-area"),
    path("areas/<uuid:area_id>/", views.get_area_detail, name="area-detail"),
    path("areas/<uuid:area_id>/insight/", views.area_insight, name="area-insight"),
    path("areas/<uuid:area_id>/hyperlocal/", views.refresh_hyperlocal, name="area-hyperlocal"),
    path("areas/<uuid:area_id>/tiles/", views.get_area_tiles, name="area-tiles"),
]
