"""책상 상단 카메라의 압축 최신 프레임 보관 테스트."""

from __future__ import annotations

import asyncio
import threading

from smart_desk.modules.media.workspace import WorkspaceCameraSource


JPEG_1920X1080 = (
    b"\xff\xd8"
    b"\xff\xc0\x00\x0b\x08\x04\x38\x07\x80\x01\x01\x11\x00"
    b"payload"
    b"\xff\xd9"
)


class FakeCapture:
    def __init__(self, packet: bytes) -> None:
        self.packet = packet
        self.closed = threading.Event()

    def demux(self, *, video: int):
        assert video == 0
        yield self.packet
        self.closed.wait(2)

    def close(self) -> None:
        self.closed.set()


async def test_workspace_camera_keeps_only_latest_compressed_jpeg() -> None:
    capture = FakeCapture(b"prefix" + JPEG_1920X1080 + b"suffix")
    received: dict[str, object] = {}

    def factory(device: str, **options: object) -> FakeCapture:
        received.update({"device": device, **options})
        return capture

    source = WorkspaceCameraSource(
        device="/dev/workspace-cam",
        input_format="mjpeg",
        width=2592,
        height=1944,
        fps=15,
        capture_factory=factory,
    )
    await source.start()
    for _ in range(100):
        if source.get_latest_snapshot() is not None:
            break
        await asyncio.sleep(0.001)

    snapshot = source.get_latest_snapshot()
    assert snapshot is not None
    assert snapshot.jpeg == JPEG_1920X1080
    assert (snapshot.width, snapshot.height) == (1920, 1080)
    assert snapshot.age_seconds() >= 0
    assert source.is_connected() is True
    assert received == {
        "device": "/dev/workspace-cam",
        "input_format": "mjpeg",
        "width": 2592,
        "height": 1944,
        "fps": 15,
    }

    await source.stop()

    assert capture.closed.is_set()
    assert source.get_latest_snapshot() is None
    assert source.is_connected() is False
