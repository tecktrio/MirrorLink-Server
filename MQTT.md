# MQTT playback configuration

The server publishes playback events to `mirror/<mirror ObjectId>`. The viewer subscribes to the same topic after its mirror login response.

Configure the server environment with:

- `MQTT_BROKER_HOST` (required for publishing)
- `MQTT_BROKER_PORT` (optional, defaults to `1883`)
- `MQTT_USERNAME` and `MQTT_PASSWORD` (optional)

Set `window.MIRRORLINK_MQTT_URL` in `MirrorLink-Viewer/index.html` to the broker's browser WebSocket endpoint. The broker must expose both the configured server listener and a WebSocket listener, and allow authenticated viewers to subscribe to their mirror topic. `paho-mqtt` is included in the server requirements.
