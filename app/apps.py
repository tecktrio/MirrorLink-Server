import os
import socket
import threading
import time
from django.apps import AppConfig as DjangoAppConfig


class AppConfig(DjangoAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app'

    def ready(self):
        if os.environ.get('MIRRORLINK_DISABLE_VIRTUAL_SCREENS') == '1':
            return
        threading.Thread(
            target=self._run_virtual_screen_supervisor,
            name='mirrorlink-virtual-screen-supervisor',
            daemon=True
        ).start()

    @staticmethod
    def _run_virtual_screen_supervisor():
        """Start a headless playback clock for each registered mirror at boot."""
        owner = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            try:
                from app.conductor import get_or_create_conductor
                from app.models import mirror_collection

                now = time.time()
                mirrors = mirror_collection.find({
                    '$or': [
                        {'playback_conductor_owner': owner},
                        {'playback_conductor_lease_until': {'$lt': now}},
                        {'playback_conductor_lease_until': {'$exists': False}}
                    ]
                }, {'_id': 1})
                for mirror in mirrors:
                    get_or_create_conductor(mirror['_id']).start()
            except Exception as exc:
                print(f"[VirtualScreen] Startup scan failed: {exc}", flush=True)
            time.sleep(15)
