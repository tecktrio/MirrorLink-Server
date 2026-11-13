import time
import json
import queue
import threading
from bson.objectid import ObjectId
from app.models import mirror_collection, content_collection


class MirrorConductor:
    """
    Central Playback Conductor for Digital Signage Displays.
    The server owns the master playback timeline and triggers slide transitions
    across all connected display devices simultaneously via Server-Sent Events (SSE).
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

    def add_listener(self, client_queue):
        with self.lock:
            self.listeners.add(client_queue)
            if not self.is_running or not self.thread or not self.thread.is_alive():
                self.is_running = True
                self.thread = threading.Thread(target=self._run_loop, daemon=True)
                self.thread.start()

    def remove_listener(self, client_queue):
        with self.lock:
            self.listeners.discard(client_queue)

    def has_listeners(self):
        with self.lock:
            return len(self.listeners) > 0

    def broadcast(self, event_type, data):
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

    def get_initial_state(self, mirror):
        contents = self.get_active_contents(mirror)
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
        while True:
            try:
                if not self.has_listeners():
                    idle_count += 1
                    if idle_count > 30:  # 30 seconds with 0 connected displays -> shut down background loop
                        break
                    time.sleep(1.0)
                    continue
                else:
                    idle_count = 0

                mirror = mirror_collection.find_one({'_id': self.mirror_id})
                if not mirror:
                    time.sleep(2.0)
                    continue

                # Detect playlist updates (added, reordered, or removed content)
                last_refresh = mirror.get('last_refresh_timestamp', 0)
                if self.last_known_refresh != 0 and last_refresh > self.last_known_refresh:
                    self.last_known_refresh = last_refresh
                    fresh_contents = self.get_active_contents(mirror)
                    self.broadcast('update_playlist', {'data': fresh_contents})
                else:
                    self.last_known_refresh = last_refresh

                playback_state = mirror.get('playback_state', 'playing')
                if playback_state == 'paused':
                    self.interrupt_event.wait(timeout=1.0)
                    self.interrupt_event.clear()
                    continue

                contents = self.get_active_contents(mirror)
                if not contents:
                    self.broadcast('standby', {
                        'mirror_id': str(self.mirror_id),
                        'mirror_name': mirror.get('mirror_name', ''),
                        'message': 'No media assigned to this mirror'
                    })
                    self.interrupt_event.wait(timeout=5.0)
                    self.interrupt_event.clear()
                    continue

                if self.current_index >= len(contents):
                    self.current_index = 0

                current_item = contents[self.current_index]
                photo_duration = mirror.get('photo_duration', 5)
                content_durations = mirror.get('content_durations', {})
                cid = str(current_item.get('_id'))

                item_duration = int(content_durations.get(cid, current_item.get('duration') or photo_duration or 5))
                item_duration = max(2, item_duration)

                now = time.time()
                mirror_collection.update_one(
                    {'_id': self.mirror_id},
                    {'$set': {
                        'active_content_id': cid,
                        'active_content_index': self.current_index,
                        'sync_timestamp': now,
                        'last_active_timestamp': now
                    }}
                )

                # Broadcast slide_change trigger to all connected mirror displays simultaneously
                self.broadcast('slide_change', {
                    'index': self.current_index,
                    'content_id': cid,
                    'content_url': current_item.get('content_url'),
                    'content_format': current_item.get('content_format'),
                    'duration': item_duration,
                    'timestamp': now
                })

                # Sleep for the duration of the current slide, or interrupt if an admin pauses/edits
                interrupted = self.interrupt_event.wait(timeout=item_duration)
                if interrupted:
                    self.interrupt_event.clear()
                    continue

                # Move to next slide
                self.current_index = (self.current_index + 1) % len(contents)

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

