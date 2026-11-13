# MQTT playback configuration

At Django startup, the server scans registered mirrors and starts a headless playback clock for each one, without waiting for a physical display login. It publishes playback events to `mirror/<mirror ObjectId>`. Physical viewers and the web virtual screen subscribe to that topic.

Configure the server environment with either `MQTT_BROKER_HOST` (host only) and `MQTT_BROKER_PORT`, or `MQTT_BROKER_URL` (for example `mqtt://broker:1883` or `mqtts://broker:8883`):

- `MQTT_BROKER_HOST` (required for publishing)
- `MQTT_BROKER_PORT` (optional, defaults to `1883`)
- `MQTT_USERNAME` and `MQTT_PASSWORD` (optional)

The server logs `[VirtualScreen] Playback conductor started` and `[VirtualScreen] Conductor lease acquired` when a worker takes responsibility for a mirror. Only one Uvicorn worker publishes a given mirror's timeline; other workers supervise and can take over after a lease expires. It logs `[MQTT] Not publishing` when broker configuration is missing, and logs connection/queued-trigger status once configured. Install the updated server requirements so `paho-mqtt` is present, then restart every Uvicorn worker after changing environment variables. Set `MIRRORLINK_DISABLE_VIRTUAL_SCREENS=1` only to disable automatic server-side playback.

Set `window.MIRRORLINK_MQTT_URL` in `MirrorLink-Viewer/index.html` to the broker's browser WebSocket endpoint. The broker must expose both the configured server listener and a WebSocket listener, and allow authenticated viewers to subscribe to their mirror topic. `paho-mqtt` is included in the server requirements.

If browser clients need MQTT connection options, set `window.MIRRORLINK_MQTT_OPTIONS` in the viewer and web app HTML configuration (for example, username/password or a broker-issued short-lived credential). Apply broker ACLs so a display can subscribe only to its assigned `mirror/<id>` topic.
