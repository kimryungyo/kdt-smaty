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
