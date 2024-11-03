import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from mirrorlink_server.routing import websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mirrorlink_server.settings')
import firebase_config 
application = ProtocolTypeRouter({
    "http": get_asgi_application(),  # Handles HTTP requests with ASGI app
    "websocket": AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns  # Routes WebSocket connections
        )
    ),
})