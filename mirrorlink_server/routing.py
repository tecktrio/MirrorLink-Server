from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'mirror', consumers.MirrorConsumer.as_asgi()),  # Room-based chat
    re_path(r'controller', consumers.ControllerConsumer.as_asgi()),  # Room-based chat
    # re_path(r'^ws/notifications/$', consumers.NotificationConsumer.as_asgi()),  # Notifications
]