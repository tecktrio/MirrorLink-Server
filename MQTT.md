# MQTT playback configuration

At Django startup, the server scans registered mirrors and starts a headless playback clock for each one, without waiting for a physical display login. It publishes playback events to `mirror/<mirror ObjectId>`. Physical viewers and the web virtual screen subscribe to that topic.

Configure the server environment with either `MQTT_BROKER_HOST` (host only) and `MQTT_BROKER_PORT`, or `MQTT_BROKER_URL` (for example `mqtt://broker:1883` or `mqtts://broker:8883`):

- `MQTT_BROKER_HOST` (required for publishing)
- `MQTT_BROKER_PORT` (optional, defaults to `1883`)
- `MQTT_USERNAME` and `MQTT_PASSWORD` (optional)

The server logs `[VirtualScreen] Playback conductor started` and `[VirtualScreen] Conductor lease acquired` when a worker takes responsibility for a mirror. Only one Uvicorn worker publishes a given mirror's timeline; other workers supervise and can take over after a lease expires. It logs `[MQTT] Not publishing` when broker configuration is missing, and logs connection/queued-trigger status once configured. Install the updated server requirements so `paho-mqtt` is present, then restart every Uvicorn worker after changing environment variables. Set `MIRRORLINK_DISABLE_VIRTUAL_SCREENS=1` only to disable automatic server-side playback.

Set `window.MIRRORLINK_MQTT_URL` in `MirrorLink-Viewer/index.html` to the broker's browser WebSocket endpoint. The broker must expose both the configured server listener and a WebSocket listener, and allow authenticated viewers to subscribe to their mirror topic. `paho-mqtt` is included in the server requirements.

## Mosquitto and Nginx WebSocket example

The current viewer URL `wss://mirrorlink.bigburry.com/mqtt` works only if Nginx upgrades WebSocket requests and proxies them to a Mosquitto listener. A `200 OK` handshake response means the request is currently reaching an ordinary HTTP handler instead.

On the broker, keep the server's local MQTT listener and add a WebSocket listener (Mosquitto supports `protocol websockets` on a listener):

```conf
listener 1883 127.0.0.1
protocol mqtt

listener 9001 127.0.0.1
protocol websockets
```

Inside the TLS `server` block for `mirrorlink.bigburry.com`, route `/mqtt` to that listener before the site's normal HTTP proxy/static locations:

```nginx
location = /mqtt {
    proxy_pass http://127.0.0.1:9001/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
    proxy_buffering off;
}
```

Restart Mosquitto and reload Nginx after applying the deployment configuration. Keep port 9001 bound to loopback; browsers connect through HTTPS/WSS on port 443. If your broker is not Mosquitto, configure its equivalent WebSocket listener and proxy path.

If browser clients need MQTT connection options, set `window.MIRRORLINK_MQTT_OPTIONS` in the viewer and web app HTML configuration (for example, username/password or a broker-issued short-lived credential). Apply broker ACLs so a display can subscribe only to its assigned `mirror/<id>` topic.
