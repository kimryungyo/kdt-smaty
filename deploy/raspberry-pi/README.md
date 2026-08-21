# Raspberry Pi CSI camera publisher

The CSI camera publishes `bottom-cam` through WHIP/WebRTC. It fixes the
`2304x1296` sensor mode that the original 1920x1080 stream selected, while the
camera ISP outputs 640x360 at 4 fps. Therefore the field of view is retained
without a CPU-side resize.

Install the dependencies (`rpicam-apps`, GStreamer and the WHIP client plugin),
then install and enable the unit:

```bash
sudo install -D -m 755 deploy/raspberry-pi/rpi-camera-stream /usr/local/sbin/rpi-camera-whip
sudo install -D -m 644 deploy/raspberry-pi/rpi-camera-stream.service /etc/systemd/system/rpi-camera-stream.service
sudo mkdir -p /etc/smart-desk
sudo sh -c 'printf "MEDIAMTX_HOST=127.0.0.1\n" > /etc/smart-desk/rpi-camera-stream.env'
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-camera-stream.service
```

The production Raspberry Pi runs MediaMTX locally, so the publisher uses the
loopback address. Do not probe the CSI camera with a second `rpicam-*` process
while this unit is active; stop the unit first when camera diagnostics are
required.

## Production stack

The Raspberry Pi is the production owner of Main, EMQX, MediaMTX and the USB
camera relays. Run Compose from the repository root so the deployment `.env`
is explicit:

```bash
docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  config --quiet
docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  up -d --build
```

The Pi host exposes Main on `:9090`, voice debug on `:10000`, EMQX on `:1883`,
MediaMTX WebRTC on TCP `:8889` and UDP `:8189`, `user-cam` MJPEG on `:10001`,
and no public workspace-camera stream. Main keeps `/dev/workspace-cam` open and
retains only its latest compressed JPEG for the Realtime `inspect_workspace` tool.
Main also opens the CH340 height reader by its stable `/dev/serial/by-id` path.
Both ESP32 controllers use MQTT in
production; their USB serial paths remain available for diagnostics and
firmware upload, but are not the production control transport.

The Pi override adds Main to numeric group `44`, the appliance's `video` group,
so its non-root process can open `/dev/workspace-cam`. Reconfirm the device GID
with `stat -c '%g' /dev/workspace-cam` if the Pi image is rebuilt.

Keep both UVC cameras connected directly to the Pi USB ports on separate USB
2.0 root controllers. Connect the AKG microphone, AB13X speaker, CH340 height
reader, and relay ESP32 through the external hub. Putting both cameras behind
that hub exhausts the USB bandwidth required by the microphone.
