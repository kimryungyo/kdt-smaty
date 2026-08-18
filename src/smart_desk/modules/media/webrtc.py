"""MediaMTX WHIP/WHEP의 작은 aiortc adapter들이다.

HTTP signalling은 각 peer session의 시작/종료에만 쓰고, 영상은 WebRTC media
transport로만 흐른다. 네트워크 호출은 모두 주입 가능하게 두어 단위 테스트에서
MediaMTX나 실제 V4L2 장치가 필요하지 않다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from math import ceil, log2
import time
from typing import Any
from urllib.parse import urljoin

import numpy as np


LOGGER = logging.getLogger(__name__)
MAX_RECONNECT_INTERVAL_SECONDS = 30.0
DISCONNECTION_LOG_INTERVAL_SECONDS = 30.0
LatestFrame = tuple[np.ndarray, float]
PeerFactory = Callable[[], Any]
PlayerFactory = Callable[..., Any]
SdpExchange = Callable[[str, str], Awaitable[tuple[str, str | None]]]
SessionDelete = Callable[[str], Awaitable[None]]


def _peer_connection() -> Any:
    try:
        from aiortc import RTCConfiguration, RTCPeerConnection
    except ImportError as error:  # pragma: no cover - package dependency boundary
        raise RuntimeError("aiortc is required for WebRTC media") from error
    return RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))


def _media_player(device: str, *, input_format: str, width: int, height: int, fps: int) -> Any:
    try:
        from aiortc.contrib.media import MediaPlayer
    except ImportError as error:  # pragma: no cover - package dependency boundary
        raise RuntimeError("aiortc is required for WebRTC media") from error
    return MediaPlayer(
        device,
        format="v4l2",
        options={
            "input_format": input_format,
            "video_size": f"{width}x{height}",
            "framerate": str(fps),
        },
    )


def _rate_limited_video_track(source: Any, *, fps: int) -> Any:
    """Cap the frames handed to the WebRTC encoder.

    Some UVC cameras only expose fixed capture rates (the workspace camera
    exposes 15/30fps).  Passing a lower V4L2 ``framerate`` is then silently
    ignored.  Limiting at the outgoing track preserves the configured
    resolution while preventing needless H.264 encoding and transmission.
    """
    try:
        from aiortc import MediaStreamTrack
    except ImportError as error:  # pragma: no cover - package dependency boundary
        raise RuntimeError("aiortc is required for WebRTC media") from error

    class RateLimitedVideoTrack(MediaStreamTrack):
        kind = "video"

        def __init__(self) -> None:
            super().__init__()
            self._next_frame_at: float | None = None
            self._interval = 1.0 / max(fps, 1)

        async def recv(self) -> Any:
            if self._next_frame_at is not None:
                delay = self._next_frame_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            # MediaPlayer keeps only a small queue. Reading after the deadline
            # therefore discards stale capture frames instead of encoding them.
            frame = await source.recv()
            self._next_frame_at = time.monotonic() + self._interval
            return frame

    return RateLimitedVideoTrack()


async def _post_sdp(endpoint: str, offer_sdp: str) -> tuple[str, str | None]:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            endpoint,
            content=offer_sdp,
            headers={"Content-Type": "application/sdp", "Accept": "application/sdp"},
        )
        response.raise_for_status()
        return response.text, response.headers.get("Location")


async def _delete_session(resource_url: str) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.delete(resource_url)
        # DELETE is naturally idempotent: a server may have already expired it.
        if response.status_code not in {200, 204, 404, 410}:
            response.raise_for_status()


class _WebRtcLifecycle:
    """공통 peer lifecycle, 재시도와 인증정보를 노출하지 않는 로그를 제공한다."""

    def __init__(self, *, name: str, endpoint: str, reconnect_interval_seconds: float,
                 peer_factory: PeerFactory, exchange: SdpExchange, delete_session: SessionDelete) -> None:
        self._name = name
        self._endpoint = endpoint
        self._reconnect_interval_seconds = reconnect_interval_seconds
        self._peer_factory = peer_factory
        self._exchange = exchange
        self._delete_session = delete_session
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._session_end = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._peer: Any | None = None
        self._resource_url: str | None = None
        self._started = False
        self._connected = False
        self._last_error: str | None = None
        self._last_disconnection_log_at: float | None = None
        self._session_connected = False

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._started = True
            self._connected = False
            self._last_error = None
            self._session_connected = False
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name=f"webrtc-media-{self._name}")

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._clear_connection()

    def is_connected(self) -> bool:
        return self._connected

    def get_last_error(self) -> str | None:
        return self._last_error

    def _mark_connected(self) -> None:
        if self._connected:
            return
        self._connected = True
        self._session_connected = True
        self._last_error = None
        LOGGER.info("WebRTC media가 연결되었습니다.", extra={
            "component": "media", "event": "webrtc_connected", "camera": self._name,
        })

    def _mark_disconnected(self, error: BaseException | str) -> None:
        was_connected = self._connected
        self._clear_connection()
        if isinstance(error, str):
            message = error
        else:
            detail = str(error).strip().replace(self._endpoint, "<endpoint>")
            message = detail or type(error).__name__
        self._last_error = message[:500]
        now = time.monotonic()
        should_log = (
            was_connected or self._last_disconnection_log_at is None
            or now - self._last_disconnection_log_at >= DISCONNECTION_LOG_INTERVAL_SECONDS
        )
        self._connected = False
        if should_log:
            self._last_disconnection_log_at = now
            LOGGER.warning("WebRTC media 연결 또는 frame 수신에 실패했습니다.", extra={
                "component": "media", "event": "webrtc_disconnected", "camera": self._name,
            })

    def _clear_connection(self) -> None:
        self._connected = False

    def _retry_delay(self, failures: int) -> float:
        base = min(self._reconnect_interval_seconds, MAX_RECONNECT_INTERVAL_SECONDS)
        if base >= MAX_RECONNECT_INTERVAL_SECONDS:
            return MAX_RECONNECT_INTERVAL_SECONDS
        exponent = min(max(failures - 1, 0), ceil(log2(MAX_RECONNECT_INTERVAL_SECONDS / base)))
        return min(base * (2 ** exponent), MAX_RECONNECT_INTERVAL_SECONDS)

    async def _wait_for_end_or_stop(self) -> None:
        stop_wait = asyncio.create_task(self._stop_event.wait())
        end_wait = asyncio.create_task(self._session_end.wait())
        done, pending = await asyncio.wait({stop_wait, end_wait}, return_when=asyncio.FIRST_COMPLETED)
        del done
        for item in pending:
            item.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _cleanup_session(self) -> None:
        peer, resource = self._peer, self._resource_url
        self._peer, self._resource_url = None, None
        if peer is not None:
            try:
                await peer.close()
            except Exception:  # peer teardown must not block resource cleanup
                LOGGER.debug("WebRTC peer close failed", exc_info=True)
        if resource is not None:
            try:
                await self._delete_session(resource)
            except Exception:
                LOGGER.debug("WebRTC MediaMTX session DELETE failed", exc_info=True)


class WebRtcCameraPublisher(_WebRtcLifecycle):
    """V4L2를 PyAV MediaPlayer로 열어 MediaMTX WHIP로 발행한다."""

    def __init__(self, *, name: str, device: str, whip_url: str, input_format: str,
                 width: int, height: int, fps: int, reconnect_interval_seconds: float = 1.0,
                 peer_factory: PeerFactory = _peer_connection, player_factory: PlayerFactory = _media_player,
                 exchange: SdpExchange = _post_sdp, delete_session: SessionDelete = _delete_session) -> None:
        super().__init__(name=name, endpoint=whip_url, reconnect_interval_seconds=reconnect_interval_seconds,
                         peer_factory=peer_factory, exchange=exchange, delete_session=delete_session)
        self._device, self._input_format = device, input_format
        self._width, self._height, self._fps = width, height, fps
        self._player_factory, self._player = player_factory, None

    def is_running(self) -> bool:
        return self._started and self._task is not None and not self._task.done()

    async def _run(self) -> None:
        failures = 0
        try:
            while not self._stop_event.is_set():
                self._session_end = asyncio.Event()
                self._session_connected = False
                try:
                    await self._open_session()
                    await self._wait_for_end_or_stop()
                    if not self._stop_event.is_set():
                        self._mark_disconnected("WebRTC peer connection ended.")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._mark_disconnected(error)
                finally:
                    await self._cleanup_publisher()
                if self._stop_event.is_set():
                    break
                if self._session_connected:
                    failures = 0
                failures += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), self._retry_delay(failures))
                except TimeoutError:
                    pass
        finally:
            await self._cleanup_publisher()

    async def _open_session(self) -> None:
        self._player = self._player_factory(self._device, input_format=self._input_format,
                                            width=self._width, height=self._height, fps=self._fps)
        source_video = getattr(self._player, "video", None)
        if source_video is None:
            raise RuntimeError("V4L2 camera did not provide a video track")
        video = _rate_limited_video_track(source_video, fps=self._fps)
        peer = self._peer_factory()
        self._peer = peer
        self._observe_peer(peer)
        sender = peer.addTrack(video)
        self._prefer_h264(peer, sender)
        on_track_event = getattr(video, "on", None)
        if callable(on_track_event):
            @on_track_event("ended")
            async def on_track_ended() -> None:
                self._session_end.set()
        offer = await peer.createOffer()
        await peer.setLocalDescription(offer)
        answer_sdp, location = await self._exchange(self._endpoint, peer.localDescription.sdp)
        self._resource_url = urljoin(self._endpoint, location) if location else None
        await peer.setRemoteDescription(self._session_description(answer_sdp))

    @staticmethod
    def _session_description(sdp: str) -> Any:
        from aiortc import RTCSessionDescription
        return RTCSessionDescription(sdp=sdp, type="answer")

    def _observe_peer(self, peer: Any) -> None:
        @peer.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if peer.connectionState == "connected":
                self._mark_connected()
            elif peer.connectionState in {"closed", "failed", "disconnected"}:
                self._session_end.set()

    @staticmethod
    def _prefer_h264(peer: Any, sender: Any) -> None:
        """카메라 두 대의 CPU 비용을 예측 가능하게 H264로 고정한다."""

        get_transceivers = getattr(peer, "getTransceivers", None)
        if not callable(get_transceivers):
            return
        from aiortc import RTCRtpSender

        codecs = [
            codec
            for codec in RTCRtpSender.getCapabilities("video").codecs
            if codec.mimeType.lower() == "video/h264"
        ]
        for transceiver in get_transceivers():
            if transceiver.sender is sender:
                transceiver.setCodecPreferences(codecs)
                return

    async def _cleanup_publisher(self) -> None:
        await self._cleanup_session()
        player, self._player = self._player, None
        if player is not None:
            try:
                video = getattr(player, "video", None)
                if video is not None:
                    video.stop()
                audio = getattr(player, "audio", None)
                if audio is not None:
                    audio.stop()
            except Exception:
                LOGGER.debug("WebRTC MediaPlayer stop failed", exc_info=True)


class WebRtcFrameSource(_WebRtcLifecycle):
    """MediaMTX WHEP에서 최신 BGR numpy frame 하나만 보관한다."""

    def __init__(self, *, name: str, whep_url: str, reconnect_interval_seconds: float = 1.0,
                 peer_factory: PeerFactory = _peer_connection, exchange: SdpExchange = _post_sdp,
                 delete_session: SessionDelete = _delete_session) -> None:
        super().__init__(name=name, endpoint=whep_url, reconnect_interval_seconds=reconnect_interval_seconds,
                         peer_factory=peer_factory, exchange=exchange, delete_session=delete_session)
        self._latest_frame: LatestFrame | None = None
        self._track_task: asyncio.Task[None] | None = None

    def get_latest_frame(self) -> LatestFrame | None:
        return self._latest_frame

    def _clear_connection(self) -> None:
        super()._clear_connection()
        self._latest_frame = None

    async def _run(self) -> None:
        failures = 0
        try:
            while not self._stop_event.is_set():
                self._session_end = asyncio.Event()
                self._session_connected = False
                try:
                    await self._open_session()
                    await self._wait_for_end_or_stop()
                    if not self._stop_event.is_set():
                        self._mark_disconnected("WebRTC peer connection ended.")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._mark_disconnected(error)
                finally:
                    await self._cleanup_source()
                if self._stop_event.is_set():
                    break
                if self._session_connected:
                    failures = 0
                failures += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), self._retry_delay(failures))
                except TimeoutError:
                    pass
        finally:
            await self._cleanup_source()

    async def _open_session(self) -> None:
        peer = self._peer_factory()
        self._peer = peer
        self._observe_peer(peer)
        peer.addTransceiver("video", direction="recvonly")
        offer = await peer.createOffer()
        await peer.setLocalDescription(offer)
        answer_sdp, location = await self._exchange(self._endpoint, peer.localDescription.sdp)
        self._resource_url = urljoin(self._endpoint, location) if location else None
        await peer.setRemoteDescription(self._session_description(answer_sdp))

    @staticmethod
    def _session_description(sdp: str) -> Any:
        from aiortc import RTCSessionDescription
        return RTCSessionDescription(sdp=sdp, type="answer")

    def _observe_peer(self, peer: Any) -> None:
        @peer.on("track")
        def on_track(track: Any) -> None:
            if getattr(track, "kind", None) == "video":
                self._track_task = asyncio.create_task(self._consume_video(track))

        @peer.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if peer.connectionState in {"closed", "failed", "disconnected"}:
                self._session_end.set()

    async def _consume_video(self, track: Any) -> None:
        try:
            while not self._stop_event.is_set():
                frame = await track.recv()
                self._latest_frame = (frame.to_ndarray(format="bgr24"), time.monotonic())
                # WHEP is usable only after the first actual video frame.
                self._mark_connected()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self._stop_event.is_set():
                self._mark_disconnected(error)
                self._session_end.set()

    async def _cleanup_source(self) -> None:
        task, self._track_task = self._track_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._cleanup_session()
