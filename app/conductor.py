import time
import json
import queue
import threading
import os
import socket
from urllib.parse import urlparse
from bson.objectid import ObjectId
from app.models import administrators_collection, mirror_collection, content_collection


class MirrorConductor:
    """
    Central Playback Conductor for Digital Signage Displays.
    The server owns the master playback timeline and publishes slide transitions
    to mirror MQTT topics (and legacy SSE listeners when present).
    """

    def __init__(self, mirror_id):
        self.mirror_id = ObjectId(mirror_id) if not isinstance(mirror_id, ObjectId) else mirror_id
        self.listeners = set()
        self.lock = threading.Lock()
        self.thread = None
        self.is_running = False
        self.interrupt_event = threading.Event()
        self.current_index = 0
        self.last_known_refresh = 0
        self.mqtt_client = None
        self.mqtt_missing_config_logged = False
        self.conductor_owner = f"{socket.gethostname()}:{os.getpid()}"
        self.lease_wait_logged = False
        self.last_published_content_id = None

    def _mqtt_connected(self, client, userdata, flags, rc, *extra):
        if rc == 0:
            print(f"[MQTT] Broker connected for mirror {self.mirror_id}", flush=True)
        else:
            print(f"[MQTT] Broker rejected connection for mirror {self.mirror_id}: rc={rc}", flush=True)

    def _mqtt_published(self, client, userdata, mid, *extra):
        print(f"[MQTT] Broker acknowledged publish mirror={self.mirror_id} mid={mid}", flush=True)

    def _eligibility_status(self):
        """Return the mirror status event when playback is not eligible."""
        mirror = mirror_collection.find_one({'_id': self.mirror_id}, {'administrator_id': 1, 'is_active': 1, 'status': 1})
        if not mirror:
            return {'status_code': 500, 'status_text': 'Subscribed account not found'}
        mirror_status = str(mirror.get('status', '')).strip().lower()
        if mirror.get('is_active') is False or mirror_status in ('inactive', 'disabled'):
            return {'status_code': 600, 'status_text': 'Mirror is inactive'}
        administrator_id = mirror.get('administrator_id')
        owner = None
        if administrator_id is not None:
            owner = administrators_collection.find_one({'_id': administrator_id}, {'subscription': 1})
            if owner is None:
                owner = administrators_collection.find_one({'_id': str(administrator_id)}, {'subscription': 1})
        subscription = (owner or {}).get('subscription', {})
        if subscription.get('status') != 'active' or not subscription.get('plan_id'):
            return {'status_code': 500, 'status_text': 'Subscribed account not found'}
        return None

    def publish(self, event_type, data, retain=False):
        """Publish a JSON event to every display subscribed to this mirror."""
        # Check eligibility at the publish boundary so every MQTT event path,
        # including retained playback state, observes account and mirror changes.
        try:
            status = self._eligibility_status()
        except Exception as exc:
            print(f"[MQTT] Eligibility check failed for mirror {self.mirror_id}: {exc}", flush=True)
            status = {'status_code': 500, 'status_text': 'Subscribed account not found'}
        if status:
            event_type, data, retain = 'eligibility_status', status, True

        broker = os.environ.get('MQTT_BROKER_HOST')
        broker_url = os.environ.get('MQTT_BROKER_URL')
        parsed = None
        secure = False
        if broker_url:
            parsed = urlparse(broker_url if '://' in broker_url else f'mqtt://{broker_url}')
            broker = broker or parsed.hostname
            secure = parsed.scheme in ('mqtts', 'ssl', 'tls')
        if not broker:
            if not self.mqtt_missing_config_logged:
                print(
                    f"[MQTT] Not publishing for mirror {self.mirror_id}: set MQTT_BROKER_HOST or MQTT_BROKER_URL",
                    flush=True
                )
                self.mqtt_missing_config_logged = True
            return
        try:
            import paho.mqtt.client as mqtt
            if self.mqtt_client is None:
                client_id = f"mirrorlink-server-{str(self.mirror_id)}"
                try:
                    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
                except (AttributeError, TypeError):
                    client = mqtt.Client(client_id=client_id)
                username = os.environ.get('MQTT_USERNAME')
                password = os.environ.get('MQTT_PASSWORD')
                if username:
                    client.username_pw_set(username, password)
                if secure:
                    client.tls_set()
                default_port = 8883 if secure else 1883
                port = int(os.environ.get('MQTT_BROKER_PORT') or (parsed.port if parsed else None) or default_port)
                client.on_connect = self._mqtt_connected
                client.on_publish = self._mqtt_published
                print(f"[MQTT] Connecting to {broker}:{port} for mirror {self.mirror_id}", flush=True)
                client.connect_async(broker, port, keepalive=30)
                client.loop_start()
                self.mqtt_client = client
            topic = f"mirror/{self.mirror_id}"
            info = self.mqtt_client.publish(topic, json.dumps({'event': event_type, 'data': data}), qos=1, retain=retain)
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"[MQTT] Queued event={event_type} topic={topic} qos=1 retain={retain}", flush=True)
            elif info.rc == mqtt.MQTT_ERR_NO_CONN:
                print(f"[MQTT] Waiting for broker connection before event={event_type} topic={topic}", flush=True)
            else:
                print(f"[MQTT] Publish rejected event={event_type} topic={topic} rc={info.rc}", flush=True)
        except Exception as exc:
            print(f"[MQTT] Publish error mirror={self.mirror_id} event={event_type}: {exc}", flush=True)

    def add_listener(self, client_queue):
        with self.lock:
            self.listeners.add(client_queue)
        self.start()

    def start(self):
        """Start playback for this mirror if this worker owns its distributed lease."""
        mirror = mirror_collection.find_one({'_id': self.mirror_id})
        if not mirror or not self._acquire_playback_lease(mirror):
            return False
        with self.lock:
            if not self.is_running or not self.thread or not self.thread.is_alive():
                mirror_collection.update_one(
                    {'_id': self.mirror_id},
                    {'$set': {'virtual_screen_active': True, 'virtual_screen_started_at': time.time()}}
                )
                self.is_running = True
                self.thread = threading.Thread(target=self._run_loop, daemon=True)
                self.thread.start()
                print(f"[VirtualScreen] Playback conductor started for mirror {self.mirror_id}", flush=True)
        return True

    def remove_listener(self, client_queue):
        with self.lock:
            self.listeners.discard(client_queue)

    def has_listeners(self):
        with self.lock:
            return len(self.listeners) > 0

    def broadcast(self, event_type, data):
        self.publish(event_type, data, retain=event_type in ('init', 'standby', 'slide_change', 'playback_control'))
        dead_queues = []
        with self.lock:
            for q in list(self.listeners):
                try:
                    q.put_nowait({'event': event_type, 'data': data})
                except queue.Full:
                    dead_queues.append(q)
                except Exception:
                    dead_queues.append(q)
            for q in dead_queues:
                self.listeners.discard(q)

    def notify_state_change(self):
        """Wakes up the conductor loop immediately when an admin action occurs."""
        self.interrupt_event.set()

    def _acquire_playback_lease(self, mirror):
        """Elect one Uvicorn worker to publish a mirror's playback timeline."""
        now = time.time()
        # The loop renews this lease while waiting on long media slots.
        lease_seconds = 60

        current_owner = mirror.get('playback_conductor_owner')
        lease_until = float(mirror.get('playback_conductor_lease_until', 0) or 0)
        if current_owner == self.conductor_owner and lease_until > now + lease_seconds / 2:
            return True

        result = mirror_collection.update_one(
            {
                '_id': self.mirror_id,
                '$or': [
                    {'playback_conductor_owner': self.conductor_owner},
                    {'playback_conductor_lease_until': {'$lt': now}},
                    {'playback_conductor_lease_until': {'$exists': False}}
                ]
            },
            {'$set': {
                'playback_conductor_owner': self.conductor_owner,
                'playback_conductor_lease_until': now + lease_seconds
            }}
        )
        if result.matched_count:
            if current_owner != self.conductor_owner:
                print(f"[VirtualScreen] Conductor lease acquired mirror={self.mirror_id} owner={self.conductor_owner}", flush=True)
            self.lease_wait_logged = False
            return True

        if not self.lease_wait_logged:
            print(f"[VirtualScreen] Another worker owns mirror={self.mirror_id}; this worker will not publish its timeline", flush=True)
            self.lease_wait_logged = True
        return False

    def get_active_contents(self, mirror):
        disabled_set = {str(d) for d in mirror.get('disabled_contents', [])}
        content_durations = mirror.get('content_durations', {})
        photo_duration = mirror.get('photo_duration', 5)
        content_list = []
        for cid in mirror.get('contents', []):
            if str(cid) in disabled_set:
                continue
            try:
                c_doc = content_collection.find_one({'_id': ObjectId(cid)})
                if c_doc:
                    c = dict(c_doc)
                    for k, v in list(c.items()):
                        if isinstance(v, ObjectId):
                            c[k] = str(v)
                        elif hasattr(v, 'isoformat'):
                            c[k] = v.isoformat()
                    c['duration'] = int(content_durations.get(str(cid), photo_duration))
                    content_list.append(c)
            except Exception:
                pass
        return content_list

    def calculate_timeline_slot(self, contents, mirror):
        if not contents:
            return 0, 5, time.time()
        photo_duration = mirror.get('photo_duration', 5)
        content_durations = mirror.get('content_durations', {})
        durations = []
        for c in contents:
            cid = str(c.get('_id'))
            dur = int(content_durations.get(cid, c.get('duration') or photo_duration or 5))
            durations.append(max(2, dur))
        total_cycle = sum(durations)
        if total_cycle <= 0:
            total_cycle = len(contents) * 5
        now = time.time()
        pos = now % total_cycle
        cum = 0
        for idx, dur in enumerate(durations):
            if cum + dur > pos:
                remaining = (cum + dur) - pos
                return idx, max(0.2, remaining), now
            cum += dur
        return 0, durations[0], now

    def get_initial_state(self, mirror):
        contents = self.get_active_contents(mirror)
        current_idx, remaining, _ = self.calculate_timeline_slot(contents, mirror)
        self.current_index = current_idx
        return {
            'status_text': 'ok',
            'status_code': 200,
            'mirror_id': str(mirror.get('_id')),
            'mirror_name': mirror.get('mirror_name', ''),
            'photo_duration': mirror.get('photo_duration', 5),
            'active_content_index': self.current_index,
            'playback_state': mirror.get('playback_state', 'playing'),
            'data': contents
        }

    def _run_loop(self):
        idle_count = 0
        last_status_publish = 0
        last_eligibility_status = None
        while True:
            try:
                # The conductor is the server-side virtual screen. It runs even
                # without connected browser viewers, so media completion is known.

                mirror = mirror_collection.find_one({'_id': self.mirror_id})
                if not mirror:
                    time.sleep(2.0)
                    continue

                if not self._acquire_playback_lease(mirror):
                    self.interrupt_event.wait(timeout=3.0)
                    self.interrupt_event.clear()
                    continue

                # Keep subscribed viewers informed while blocked. The MQTT
                # publish boundary changes this event to 500/600 as needed.
                try:
                    eligibility_status = self._eligibility_status()
                except Exception as exc:
                    print(f"[MQTT] Eligibility check failed for mirror {self.mirror_id}: {exc}", flush=True)
                    eligibility_status = {'status_code': 500, 'status_text': 'Subscribed account not found'}
                if eligibility_status:
                    self.last_published_content_id = None
                    last_eligibility_status = eligibility_status
                    if time.time() - last_status_publish >= 3:
                        self.publish('eligibility_status', eligibility_status, retain=True)
                        last_status_publish = time.time()
                    self.interrupt_event.wait(timeout=3.0)
                    self.interrupt_event.clear()
                    continue

                # Replace the retained 500/600 status as soon as playback is
                # eligible again. The full state lets a connected viewer
                # recover immediately, including when playback is paused or
                # the playlist is currently empty.
                if last_eligibility_status:
                    initial_state = self.get_initial_state(mirror)
                    self.broadcast('init', initial_state)
                    active_contents = initial_state.get('data', [])
                    if active_contents and mirror.get('playback_state', 'playing') != 'paused':
                        index = max(0, min(self.current_index, len(active_contents) - 1))
                        self.last_published_content_id = str(active_contents[index].get('_id'))
                    else:
                        self.last_published_content_id = None
                    last_eligibility_status = None

                contents = self.get_active_contents(mirror)

                # Detect playlist updates (added, reordered, or removed content)
                last_refresh = mirror.get('last_refresh_timestamp', 0)
                if self.last_known_refresh != 0 and last_refresh > self.last_known_refresh:
                    self.last_known_refresh = last_refresh
                    fresh_contents = self.get_active_contents(mirror)
                    self.broadcast('update_playlist', {'data': fresh_contents})
                    self.last_published_content_id = None
                else:
                    self.last_known_refresh = last_refresh

                playback_state = mirror.get('playback_state', 'playing')
                if playback_state == 'paused':
                    self.interrupt_event.wait(timeout=1.0)
                    self.interrupt_event.clear()
                    continue

                if not contents:
                    self.last_published_content_id = None
                    self.broadcast('standby', {
                        'mirror_id': str(self.mirror_id),
                        'mirror_name': mirror.get('mirror_name', ''),
                        'message': 'No media assigned to this mirror'
                    })
                    self.interrupt_event.wait(timeout=5.0)
                    self.interrupt_event.clear()
                    continue

                # Universal Epoch Sync: Calculate current active slide and remaining time in this slot
                slot_idx, remaining, now = self.calculate_timeline_slot(contents, mirror)
                self.current_index = slot_idx
                current_item = contents[self.current_index]
                cid = str(current_item.get('_id'))

                mirror_collection.update_one(
                    {'_id': self.mirror_id},
                    {'$set': {
                        'active_content_id': cid,
                        'active_content_index': self.current_index,
                        'sync_timestamp': now,
                        'last_active_timestamp': now
                    }}
                )

                # Publish the requested content, not just its playlist index,
                # so every topic subscriber can render the same item.
                if cid != self.last_published_content_id:
                    self.broadcast('slide_change', {
                        'index': self.current_index,
                        'content_id': cid,
                        'content_url': current_item.get('content_url'),
                        'content_format': current_item.get('content_format'),
                        'content': current_item,
                        'duration': remaining,
                        'timestamp': now
                    })
                    self.last_published_content_id = cid

                # Sleep until this slide's slot ends (or until admin interrupts)
                # Keep the server's virtual playback clock aligned to actual
                # video duration when metadata is available; configured photo
                # durations remain the timing source for images.
                # Wake periodically to renew the per-mirror lease without
                # republishing the same content event.
                interrupted = self.interrupt_event.wait(timeout=max(0.5, min(15.0, remaining)))
                if interrupted:
                    self.interrupt_event.clear()
                    continue

            except Exception as e:
                print(f"[Conductor Loop Error]: {e}")
                time.sleep(2.0)

        with self.lock:
            self.is_running = False


# Conductor registry
CONDUCTORS = {}
CONDUCTORS_LOCK = threading.Lock()


def get_or_create_conductor(mirror_id):
    mid_str = str(mirror_id)
    with CONDUCTORS_LOCK:
        if mid_str not in CONDUCTORS:
            CONDUCTORS[mid_str] = MirrorConductor(mirror_id)
        return CONDUCTORS[mid_str]


def notify_mirror_updated(mirror_id):
    mid_str = str(mirror_id)
    with CONDUCTORS_LOCK:
        conductor = CONDUCTORS.get(mid_str)
        if conductor:
            conductor.notify_state_change()

