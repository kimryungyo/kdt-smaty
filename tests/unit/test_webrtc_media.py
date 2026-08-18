"""네트워크와 실제 카메라 없이 WHIP/WHEP lifecycle을 검증한다."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from smart_desk.modules.media.webrtc import WebRtcCameraPublisher, WebRtcFrameSource


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


class FakePeer:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.localDescription = type("Description", (), {"sdp": "offer-sdp"})()
        self.added_tracks: list[object] = []
        self.closed = 0
        self.connectionState = "new"
        self.transceivers: list[tuple[str, str]] = []

    def on(self, name: str):
        def register(callback):
            self.handlers[name] = callback
            return callback
        return register

    def addTrack(self, track: object) -> object:
        self.added_tracks.append(track)
        return object()

    def addTransceiver(self, kind: str, *, direction: str) -> None:
        self.transceivers.append((kind, direction))

    async def createOffer(self):
        return self.localDescription

    async def setLocalDescription(self, _offer) -> None:
        return None

    async def setRemoteDescription(self, _answer) -> None:
        return None

    async def close(self) -> None:
        self.closed += 1


class FakePlayer:
    def __init__(self) -> None:
        self.video = FakePlayerTrack()


class FakePlayerTrack:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.stopped = 0

    def on(self, name: str):
        def register(callback):
            self.handlers[name] = callback
            return callback

        return register

    def stop(self) -> None:
        self.stopped += 1


class FakeFrame:
    def to_ndarray(self, *, format: str):
        assert format == "bgr24"
        return np.full((1, 2, 3), 7, dtype=np.uint8)


class FakeTrack:
    kind = "video"

    def __init__(self) -> None:
        self.frames: asyncio.Queue[object] = asyncio.Queue()

    async def recv(self):
        item = await self.frames.get()
        if isinstance(item, BaseException):
            raise item
        return item


async def test_whip_posts_offer_uses_location_delete_and_stops_player(monkeypatch) -> None:
    peer, player = FakePeer(), FakePlayer()
    calls: list[tuple[str, str]] = []
    deleted: list[str] = []

    async def exchange(url: str, sdp: str) -> tuple[str, str | None]:
        calls.append((url, sdp))
        return "answer-sdp", "sessions/abc"

    async def delete(url: str) -> None:
        deleted.append(url)

    publisher = WebRtcCameraPublisher(
        name="user", device="/dev/video-test", whip_url="http://media:8889/user/whip",
        input_format="mjpeg", width=640, height=480, fps=15,
        peer_factory=lambda: peer, player_factory=lambda *_args, **_kwargs: player,
        exchange=exchange, delete_session=delete,
    )
    monkeypatch.setattr(publisher, "_session_description", lambda _sdp: object())

    await publisher.start()
    await wait_until(lambda: calls == [("http://media:8889/user/whip", "offer-sdp")])
    assert len(peer.added_tracks) == 1
    assert peer.added_tracks[0] is not player.video
    assert getattr(peer.added_tracks[0], "kind", None) == "video"
    assert publisher.is_running() is True

    await publisher.stop()
    await publisher.stop()
    assert deleted == ["http://media:8889/user/sessions/abc"]
    assert peer.closed == 1
    assert player.video.stopped == 1


async def test_whep_is_connected_only_after_bgr_frame_and_clears_stale_on_track_failure(monkeypatch) -> None:
    peer, track = FakePeer(), FakeTrack()
    deleted: list[str] = []

    async def exchange(_url: str, _sdp: str) -> tuple[str, str | None]:
        return "answer", "/session/one"

    async def delete(url: str) -> None:
        deleted.append(url)

    source = WebRtcFrameSource(name="posture", whep_url="http://media:8889/bottom/whep",
                               peer_factory=lambda: peer, exchange=exchange, delete_session=delete)
    monkeypatch.setattr(source, "_session_description", lambda _sdp: object())
    await source.start()
    await wait_until(lambda: source._resource_url is not None)  # noqa: SLF001
    assert peer.transceivers == [("video", "recvonly")]
    peer.handlers["track"](track)  # type: ignore[operator]
    assert source.is_connected() is False

    await track.frames.put(FakeFrame())
    await wait_until(source.is_connected)
    image, captured_at = source.get_latest_frame() or (None, None)
    assert np.array_equal(image, np.full((1, 2, 3), 7, dtype=np.uint8))
    assert isinstance(captured_at, float)

    await track.frames.put(RuntimeError("stream ended"))
    await wait_until(lambda: source.get_latest_frame() is None)
    assert source.is_connected() is False
    assert source.get_last_error() == "stream ended"
    await source.stop()
    assert deleted == ["http://media:8889/session/one"]


def test_reconnect_is_bounded_exponential() -> None:
    source = WebRtcFrameSource(name="user", whep_url="http://media:8889/user/whep",
                               reconnect_interval_seconds=0.25)
    assert [source._retry_delay(i) for i in (1, 2, 3, 100)] == [0.25, 0.5, 1.0, 30.0]  # noqa: SLF001
