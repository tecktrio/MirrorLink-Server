# MQTT playback configuration

The server publishes playback events to `mirror/<mirror ObjectId>`. The viewer subscribes to the same topic after its mirror login response.

Configure the server environment with either `MQTT_BROKER_HOST` (host only) and `MQTT_BROKER_PORT`, or `MQTT_BROKER_URL` (for example `mqtt://broker:1883` or `mqtts://broker:8883`):

- `MQTT_BROKER_HOST` (required for publishing)
- `MQTT_BROKER_PORT` (optional, defaults to `1883`)
- `MQTT_USERNAME` and `MQTT_PASSWORD` (optional)

The Django server logs `[VirtualScreen] Playback conductor started` when a mirror login starts its server-side clock. It logs `[MQTT] Not publishing` when broker configuration is missing, and logs connection/queued-trigger status once configured. Install the updated server requirements so `paho-mqtt` is present, then restart every Uvicorn worker after changing environment variables.

Set `window.MIRRORLINK_MQTT_URL` in `MirrorLink-Viewer/index.html` to the broker's browser WebSocket endpoint. The broker must expose both the configured server listener and a WebSocket listener, and allow authenticated viewers to subscribe to their mirror topic. `paho-mqtt` is included in the server requirements.

If browser clients need MQTT connection options, set `window.MIRRORLINK_MQTT_OPTIONS` in the viewer and web app HTML configuration (for example, username/password or a broker-issued short-lived credential). Apply broker ACLs so a display can subscribe only to its assigned `mirror/<id>` topic.
