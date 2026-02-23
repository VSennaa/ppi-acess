from django.urls import re_path

from alpr_app.consumers import PlacaStatusConsumer


websocket_urlpatterns = [
    re_path(r"ws/placas/status/$", PlacaStatusConsumer.as_asgi()),
]
