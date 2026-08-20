"""AI 스피커의 임시 관측 페이지와 별도 HTTP 서버를 제공한다."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import logging
from typing import Iterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from smart_desk.config.settings import VoiceDebugSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.turns import AssistantTurnStore
from smart_desk.modules.voice.audio import LocalAudioInput
from smart_desk.modules.voice.service import VoiceService
from smart_desk.modules.voice.wakeword import LiveKitWakeWordOnnxDetector


LOGGER = logging.getLogger(__name__)


class VoiceDebugView:
    """서로 다른 Voice 구성요소의 immutable 관측값을 한 응답으로 합친다."""

    def __init__(
        self,
        *,
        voice: VoiceService,
        wakeword: LiveKitWakeWordOnnxDetector,
        audio_input: LocalAudioInput,
        assistant_turns: AssistantTurnStore,
    ) -> None:
        self._voice = voice
        self._wakeword = wakeword
        self._audio_input = audio_input
        self._assistant_turns = assistant_turns

    def trigger_wake(self) -> dict[str, object]:
        """웨이크워드 인식과 동일한 turn을 강제로 시작한다(디버그/원격 전용)."""
        accepted = self._voice.trigger_wake()
        return {"accepted": accepted, "state": self._voice.get_snapshot().state}

    async def snapshot(self) -> dict[str, object]:
        turn = await self._assistant_turns.latest()
        response = None
        if turn is not None:
            response = {
                "text": turn.detail or turn.summary,
                "status": turn.status,
                "updated_at": turn.updated_at,
            }
        return {
            "voice": asdict(self._voice.get_snapshot()),
            "wakeword": asdict(self._wakeword.get_debug_snapshot()),
            "audio_input": asdict(self._audio_input.get_debug_snapshot()),
            "assistant_response": response,
        }


class _EmbeddedUvicornServer(uvicorn.Server):
    """부모 Uvicorn의 signal handler를 덮어쓰지 않는 내장 서버다."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class VoiceDebugServer:
    """기본 HTTP 서버와 분리된 포트에서 디버그 app을 실행한다."""

    def __init__(
        self,
        view: VoiceDebugView,
        settings: VoiceDebugSettings,
        task_manager: TaskManager,
    ) -> None:
        self._settings = settings
        self._task_manager = task_manager
        self._app = create_voice_debug_application(view)
        self._server: _EmbeddedUvicornServer | None = None
        self._task: asyncio.Task[object] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        config = uvicorn.Config(
            self._app,
            host=self._settings.host,
            port=self._settings.port,
            log_level="warning",
            access_log=False,
            log_config=None,
            lifespan="off",
        )
        server = _EmbeddedUvicornServer(config)
        task = self._task_manager.create(
            "voice-debug-http",
            server.serve(),
            critical=False,
        )
        self._server = server
        self._task = task
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        error = task.exception()
                        if error is not None:
                            raise RuntimeError(
                                "Voice debug HTTP 서버가 시작 전에 종료되었습니다."
                            ) from error
                        raise RuntimeError(
                            "Voice debug HTTP 서버가 시작 전에 종료되었습니다."
                        )
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.stop()
            raise
        LOGGER.info(
            "Voice debug HTTP 서버를 시작했습니다.",
            extra={"component": "voice.debug", "event": "debug_server_started"},
        )

    async def stop(self) -> None:
        server, self._server = self._server, None
        task, self._task = self._task, None
        if server is not None:
            server.should_exit = True
        if task is None:
            return
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def create_voice_debug_application(view: VoiceDebugView) -> FastAPI:
    """테스트와 내장 서버가 공유하는 read-only debug app을 만든다."""

    app = FastAPI(
        title="SMART DESK AI Speaker Debug",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        return HTMLResponse(
            DEBUG_PAGE,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, object]:
        return await view.snapshot()

    @app.post("/api/wake")
    async def wake() -> dict[str, object]:
        return view.trigger_wake()

    return app


DEBUG_PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI Speaker Debug</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, "Noto Sans KR", sans-serif; background:#0b1014; color:#eef5f7; }
    * { box-sizing:border-box; } body { margin:0; background:radial-gradient(circle at top right,#163548 0,#0b1014 38%); }
    main { width:min(1180px,calc(100% - 32px)); margin:auto; padding:32px 0 64px; }
    header { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin-bottom:22px; }
    .eyebrow { margin:0 0 7px; color:#67d9ff; font-size:.75rem; font-weight:800; letter-spacing:.13em; }
    h1,h2,p { margin-top:0; } h1 { margin-bottom:7px; font-size:clamp(1.8rem,4vw,2.7rem); }
    header p:last-child,.muted { color:#8fa1aa; }
    .header-actions { display:flex; align-items:flex-end; flex-direction:column; gap:10px; }
    nav { display:flex; gap:8px; } nav a { padding:7px 10px; border:1px solid #2d5968; border-radius:9px; color:#b9dbe5; text-decoration:none; font-size:.76rem; }
    .actions { display:flex; align-items:center; gap:10px; }
    .status { padding:8px 12px; border:1px solid #2d5968; border-radius:999px; background:#10252d; font-weight:800; }
    .status.offline { border-color:#743c43; background:#2a171a; color:#ff9da8; }
    #wakeBtn { padding:8px 14px; border:1px solid #2d5968; border-radius:999px; background:#123b4c; color:#cdeefb; font-weight:800; cursor:pointer; font-size:.85rem; }
    #wakeBtn:hover:not(:disabled) { background:#17506a; }
    #wakeBtn:disabled { opacity:.55; cursor:default; }
    .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
    article { border:1px solid #26343c; border-radius:14px; background:rgba(18,27,33,.93); padding:17px; }
    article span { color:#8fa1aa; font-size:.77rem; } article strong { display:block; margin-top:8px; font-size:1.15rem; overflow-wrap:anywhere; }
    .score { grid-column:span 2; } .score-row { display:flex; align-items:baseline; justify-content:space-between; }
    .score strong { color:#76e5ff; font-size:2.2rem; }
    progress { width:100%; height:13px; margin-top:12px; accent-color:#49cfee; }
    section { margin-top:18px; } section > h2 { margin-bottom:10px; font-size:1rem; color:#cbd9de; }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .response { white-space:pre-wrap; line-height:1.55; min-height:76px; }
    dl { display:grid; grid-template-columns:150px 1fr; gap:9px; margin:0; } dt { color:#82939c; } dd { margin:0; font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
    .privacy { margin-top:18px; padding:12px 14px; border-radius:10px; background:#271f12; color:#dfc689; font-size:.8rem; }
    @media(max-width:760px){header{align-items:flex-start;flex-direction:column}.header-actions{align-items:flex-start}.grid{grid-template-columns:repeat(2,1fr)}.score{grid-column:span 2}.two{grid-template-columns:1fr}}
    @media(max-width:440px){.grid{grid-template-columns:1fr}.score{grid-column:auto}dl{grid-template-columns:1fr;gap:3px}dd{margin-bottom:8px}}
  </style>
</head>
<body><main>
  <header><div><p class="eyebrow">TEMPORARY · PORT 10000</p><h1>AI Speaker Debug</h1><p>Wake Word와 local audio 상태를 read-only로 관측</p></div><div class="header-actions"><nav><a id="dashboardLink" href="#">대시보드</a><a id="visionLink" href="#">Vision 진단</a></nav><div class="actions"><button id="wakeBtn" type="button">강제 웨이크</button><div id="connection" class="status offline">연결 확인 중</div></div></div></header>
  <div class="grid">
    <article class="score"><div class="score-row"><div><span>HI SMARTY SCORE</span><strong id="score">--</strong></div><div><span>THRESHOLD</span><strong id="threshold">--</strong></div></div><progress id="scoreBar" max="1" value="0"></progress></article>
    <article><span>VOICE STATE</span><strong id="state">--</strong></article>
    <article><span>DETECTOR</span><strong id="armed">--</strong></article>
    <article><span>ACTIVATION STREAK</span><strong id="streak">--</strong></article>
    <article><span>FOLLOW-UP EXPIRES</span><strong id="followup">--</strong></article>
    <article><span>LAST ERROR</span><strong id="error">--</strong></article>
  </div>
  <section class="two">
    <article><h2>Microphone input</h2><dl id="audio"></dl></article>
    <article><h2>Wake Word telemetry</h2><dl id="wakewordTelemetry"></dl></article>
  </section>
  <section><article><h2>AI response</h2><p id="assistantResponse" class="response muted">아직 응답이 없습니다.</p></article></section>
  <p class="privacy">이 페이지는 최신 AI 응답 텍스트를 표시합니다. 사용자 발화, 개인 대화 문맥과 provider secrets는 표시하지 않습니다. 신뢰할 수 있는 디버그 환경에서만 사용하세요.</p>
</main><script>
  const byId=(id)=>document.getElementById(id);
  const mainUrl=new URL(window.location.href);mainUrl.port="9090";mainUrl.pathname="/";mainUrl.search="";mainUrl.hash="";byId("dashboardLink").href=mainUrl.toString();mainUrl.pathname="/debug/vision";byId("visionLink").href=mainUrl.toString();
  const text=(id,value)=>{byId(id).textContent=value ?? "--"};
  const localTime=(value)=>value ? new Date(value).toLocaleTimeString("ko-KR") : "--";
  const dbfs=(value)=>value==null ? "--" : `${value.toFixed(1)} dBFS`;
  const number=(value,digits=1)=>value==null ? "--" : value.toFixed(digits);
  const percent=(value)=>value==null ? "--" : `${(value*100).toFixed(3)}%`;
  const renderDl=(id,rows)=>{const root=byId(id);root.replaceChildren();for(const [key,value] of rows){const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=key;dd.textContent=String(value);root.append(dt,dd)}};
  const render=(data)=>{
    const w=data.wakeword,v=data.voice,a=data.audio_input,r=data.assistant_response;
    const score=w.score ?? 0;text("score",w.score==null?"warming up":w.score.toFixed(4));text("threshold",w.threshold.toFixed(2));byId("scoreBar").value=score;
    text("state",v.state);text("armed",w.armed?"ARMED":"DISARMED");text("streak",`${w.activation_streak} / ${w.consecutive_frames}`);text("followup",localTime(v.followup_expires_at));text("error",v.last_error);
    renderDl("audio",[["accepting",a.accepting],["queue",`${a.queue_size} / ${a.queue_capacity}`],["dropped",a.dropped_frames],["overflow",a.overflow_frames],["callback errors",a.callback_errors],["input RMS",dbfs(a.latest_rms_dbfs)],["input peak",dbfs(a.latest_peak_dbfs)],["recent peak",dbfs(a.recent_peak_dbfs)],["noise floor (est.)",dbfs(a.estimated_noise_floor_dbfs)],["SNR (est.)",a.estimated_snr_db==null ? "--" : `${a.estimated_snr_db.toFixed(1)} dB`],["clipping",`${percent(a.latest_clipping_ratio)} / clipped frames ${a.clipped_frames}`],["signal frames",a.signal_frames],["DC offset",a.latest_dc_offset_pcm==null ? "--" : `${number(a.latest_dc_offset_pcm)} PCM`]]);
    renderDl("wakewordTelemetry",[["recent max",number(w.recent_max_score,4)],["inference count",w.inference_count],["inference",w.last_inference_ms==null ? "--" : `last ${number(w.last_inference_ms)}ms / p50 ${number(w.inference_p50_ms)}ms / p95 ${number(w.inference_p95_ms)}ms`]]);
    text("assistantResponse",r?.text || "아직 응답이 없습니다.");
  };
  const refresh=async()=>{try{const response=await fetch("/api/snapshot",{cache:"no-store"});if(!response.ok)throw new Error(String(response.status));render(await response.json());text("connection","LIVE");byId("connection").classList.remove("offline")}catch(_){text("connection","연결 끊김");byId("connection").classList.add("offline")}};
  byId("wakeBtn").addEventListener("click",async()=>{const b=byId("wakeBtn");b.disabled=true;const original=b.textContent;try{const response=await fetch("/api/wake",{method:"POST"});const data=await response.json();b.textContent=data.accepted?"트리거됨":`무시됨 (${data.state})`;}catch(_){b.textContent="실패";}finally{setTimeout(()=>{b.textContent=original;b.disabled=false;},1200);}});
  refresh();setInterval(refresh,250);
</script></body></html>"""
