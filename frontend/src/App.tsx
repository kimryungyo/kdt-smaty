import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  controlWled,
  getAutomationStatus,
  getCurrentUser,
  getDeskStatus,
  getProfile,
  getTiltStatus,
  getVisionStatus,
  getWledStatus,
  listActivityModes,
  setActivityMode,
  setControlMode,
  setTiltTarget,
  setTarget,
  stopTilt,
  updateProfile,
  type ActivityMode,
  type Profile,
} from "./api/dashboard";
import { LED_BRIGHTNESS_MAX, LED_BRIGHTNESS_MIN } from "./config";
import { DebugPanel } from "./features/debug/DebugPanel";
import { OnScreenKeyboard } from "./features/keyboard/OnScreenKeyboard";
import { AssistantPanel } from "./features/assistant/AssistantPanel";
import { chooseGoalHeight, shouldSaveToProfile } from "./features/desk/heightGoal";
import { ProfileSettings } from "./features/profiles/ProfileSettings";
import { WorkRhythm } from "./features/report/WorkRhythm";
import { WorkRhythmCard } from "./features/report/WorkRhythmCard";
import { useSnapshotPoll } from "./hooks/useSnapshotPoll";
import { navigate, usePathname } from "./routes";
import "./styles.css";

type Panel = "profile" | "height" | "led" | "mode" | "tilt" | null;
const MIN_HEIGHT = 75;
const MAX_HEIGHT = 115;

const Icon = ({ name }: { name: Exclude<Panel, null> }) => (
  <span className="ico" aria-hidden="true">{{ profile: "◯", height: "↕", led: "✦", mode: "◷", tilt: "⌁" }[name]}</span>
);

const modeSubtitle = (mode: ActivityMode) => {
  const base = mode.description
    ? mode.description
    : mode.kind === "DEFAULT"
      ? "기본 작업 환경"
      : `앉기 ${mode.sittingHeightCm.toFixed(1)}cm · 서기 ${mode.standingHeightCm.toFixed(1)}cm`;
  return mode.tiltLevel === null ? base : `${base} · 틸트 ${mode.tiltLevel}단계`;
};

// 409는 세션 변동 외에도 릴레이 정지 실패 같은 이유로 난다. 전부 세션 문제로
// 표시하면 실제 원인(대개 장치 쪽)을 가려 진단이 어려워진다.
const CONFLICT_TEXT: Record<string, string> = {
  SESSION_MISMATCH: "현재 사용자 session이 변경되었습니다. 최신 상태를 다시 확인해 주세요.",
  DESK_STOP_FAILED: "책상을 정지시키지 못했습니다. 릴레이 연결을 확인해 주세요.",
  ACTIVE_ACTIVITY_MODE: "지금 사용 중인 작업 모드는 삭제할 수 없습니다.",
  ACTIVITY_MODE_OWNERSHIP: "다른 프로필의 작업 모드는 선택할 수 없습니다.",
};

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 409) return CONFLICT_TEXT[error.message] ?? error.message;
    if (error.status === 503) return "연결된 장치 또는 서비스가 아직 준비되지 않았습니다.";
    return error.message;
  }
  return error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
};

function SmatyDashboard() {
  const current = useSnapshotPoll(useCallback((signal) => getCurrentUser(signal), []), 1000);
  const desk = useSnapshotPoll(useCallback((signal) => getDeskStatus(signal), []), 1000);
  const wled = useSnapshotPoll(useCallback((signal) => getWledStatus(signal), []), 2000);
  const automation = useSnapshotPoll(useCallback((signal) => getAutomationStatus(signal), []), 1000);
  const tilt = useSnapshotPoll(useCallback((signal) => getTiltStatus(signal), []), 5000);
  const [panel, setPanel] = useState<Panel>(null);
  const [dark, setDark] = useState(() => localStorage.getItem("theme") === "dark");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [modes, setModes] = useState<ActivityMode[]>([]);
  const [sittingHeight, setSittingHeight] = useState(75);
  const [standingHeight, setStandingHeight] = useState(100);
  const [targetHeight, setTargetHeight] = useState(75);
  // 이번에 사용자가 직접 고친 칸. 둘 다 고쳤으면 자세로 어디로 갈지 정한다.
  const [heightEdits, setHeightEdits] = useState({ sitting: false, standing: false });
  const [ledOn, setLedOn] = useState(false);
  const [color, setColor] = useState("FFFFFF");
  const [brightness, setBrightness] = useState(128);
  const [notice, setNotice] = useState("");
  const session = current.value?.session ?? null;
  const registeredProfileId = session?.kind === "REGISTERED" ? session.profileId : null;
  const expectedSessionId = session?.sessionId ?? null;
  const selectedMode = automation.value?.activityMode ?? null;
  const automationMode = automation.value?.controlMode ?? null;
  const tiltLevels = useMemo(() => {
    const snapshot = tilt.value;
    if (!snapshot || snapshot.maxLevel < snapshot.minLevel) return [];
    return Array.from({ length: snapshot.maxLevel - snapshot.minLevel + 1 }, (_, index) => snapshot.minLevel + index);
  }, [tilt.value?.minLevel, tilt.value?.maxLevel]);
  const tiltCanMove = tilt.value?.positionValid === true && (tilt.value.status === "IDLE" || tilt.value.status === "AT_TARGET" || tilt.value.status === "STOPPED");

  const toast = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 2800);
  };

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    let live = true;
    setProfile(null); setModes([]);
    if (!registeredProfileId) return () => { live = false; };
    void Promise.all([getProfile(registeredProfileId), listActivityModes(registeredProfileId)])
      .then(([nextProfile, nextModes]) => {
        if (!live) return;
        setProfile(nextProfile); setModes(nextModes);
        setSittingHeight(nextProfile.sittingHeightCm); setStandingHeight(nextProfile.standingHeightCm);
        if (nextProfile.ledColor) setColor(nextProfile.ledColor);
      })
      .catch((error) => { if (live) toast(errorText(error)); });
    return () => { live = false; };
  }, [registeredProfileId]);

  useEffect(() => {
    const liveHeight = desk.value?.height.heightCm;
    if (liveHeight !== null && liveHeight !== undefined && panel !== "height") setTargetHeight(liveHeight);
  }, [desk.value?.height.heightCm, panel]);
  useEffect(() => {
    // 패널을 새로 열면 이번 편집 기록을 비운다.
    if (panel === "height") setHeightEdits({ sitting: false, standing: false });
  }, [panel]);
  useEffect(() => {
    const snapshot = wled.value;
    // 조명 패널을 여는 동안에는 고르는 중인 값을 장치 상태로 덮어쓰지 않는다.
    if (!snapshot || panel === "led") return;
    setLedOn(snapshot.on === true && snapshot.mode !== "OFF");
    if (snapshot.color) setColor(snapshot.color);
    if (snapshot.brightness !== null) setBrightness(snapshot.brightness);
  }, [panel, wled.value]);

  const refresh = async () => { await Promise.allSettled([current.refresh(), desk.refresh(), wled.refresh(), automation.refresh()]); };
  const close = () => setPanel(null);

  const currentHeight = desk.value?.height.heightCm ?? targetHeight;
  // 높이는 책상의 표시창을 읽어 온다. 가만히 있으면 표시창이 꺼져 새 값이 안
  // 들어오지만, 서버가 마지막 높이를 들고 있다(STALE는 이번 실행의 관측,
  // SENSOR_SLEEPING은 저장해 둔 값). 둘 다 "높이를 안다"는 뜻이므로 연결로
  // 친다. 값을 한 번도 못 받았거나(WAITING) 센서가 고장(ERROR)일 때만 아니다.
  const heightStatus = desk.value?.height.status;
  const heightKnown = desk.value?.height.heightCm !== null
    && desk.value?.height.heightCm !== undefined
    && heightStatus !== "ERROR" && heightStatus !== "WAITING";
  const relayUp = Boolean(desk.value?.relay.receivedAt) && !desk.value?.relay.lastError;
  const deskOnline = relayUp && heightKnown;
  // 책상이 실제로 움직이는 중인지. WAKING은 이동 직전 센서 확인 단계다.
  const deskBusy = desk.value?.state === "MOVING" || desk.value?.state === "WAKING";
  const deskBusyLabel = !deskBusy ? null
    : desk.value?.state === "WAKING" ? "센서 확인 중"
    : desk.value?.direction === "UP" ? "올라가는 중"
    : desk.value?.direction === "DOWN" ? "내려가는 중" : "이동 중";
  const tiltBusyLabel = tilt.value?.status === "MOVING" ? "틸팅 중" : null;

  // 앉기·서기 높이를 직접 고치면 그 값이 곧 이번에 갈 목표가 된다. 슬라이더가
  // 같이 움직여, 적용 전에 어디로 갈지 화면에서 그대로 보인다.
  const editStoredHeight = (key: "sitting" | "standing", value: number) => {
    if (key === "sitting") setSittingHeight(value); else setStandingHeight(value);
    setHeightEdits((current) => ({ ...current, [key]: true }));
    if (Number.isFinite(value) && value >= MIN_HEIGHT && value <= MAX_HEIGHT) setTargetHeight(value);
  };

  const applyHeight = async () => {
    try {
      // 둘 다 고쳤을 때만 자세를 본다. 그 외에는 슬라이더가 이미 목표를 가리킨다.
      let posture: string | null = null;
      if (heightEdits.sitting && heightEdits.standing) {
        posture = await getVisionStatus().then((status) => status.posture.status).catch(() => null);
      }
      const goal = chooseGoalHeight({
        sittingEdited: heightEdits.sitting,
        standingEdited: heightEdits.standing,
        sittingHeight, standingHeight, targetHeight, posture,
        currentHeight: desk.value?.height.heightCm ?? null,
      });
      // 자동 제어는 서버가 자세를 보고 이 높이를 계속 쓰므로 프로필에 남긴다.
      // 수동 제어는 지금 한 번만 쓰는 값이라 저장하지 않는다.
      const saving = shouldSaveToProfile(automationMode);
      if (saving && profile) {
        setProfile(await updateProfile(profile.id, {
          sittingHeightCm: sittingHeight, standingHeightCm: standingHeight,
        }));
      }
      // 자동 제어에서 직접 목표를 주면 서버가 그 순간 수동으로 바꾼다. 대신
      // 작업 모드를 다시 선택해 바뀐 프로필 높이를 읽게 하면, 자동을 유지한
      // 채로 지금 자세에 맞는 높이로 옮겨 간다.
      const refreshedInAuto = saving && selectedMode !== null && expectedSessionId !== null;
      if (refreshedInAuto) {
        await setActivityMode(selectedMode.key, expectedSessionId);
      } else {
        await setTarget(goal);
      }
      await Promise.allSettled([desk.refresh(), automation.refresh()]);
      close();
      toast(!saving
        ? `${goal.toFixed(1)}cm로 이동해요. 이번만 쓰는 높이라 저장하지 않았어요.`
        : refreshedInAuto
          ? "프로필에 저장했어요. 자세에 맞춰 책상이 옮겨 갑니다."
          : `${goal.toFixed(1)}cm로 이동하고 프로필에 저장했어요.`);
    } catch (error) { toast(errorText(error)); }
  };

  const changeControlMode = async (next: "AUTO" | "MANUAL") => {
    if (!expectedSessionId) { toast("현재 사용자 session이 있어야 제어 방식을 바꿀 수 있어요."); return; }
    try { await setControlMode(next, expectedSessionId); await automation.refresh(); }
    catch (error) { await refresh(); toast(errorText(error)); }
  };

  const applyLed = async () => {
    try {
      const session = expectedSessionId ? { expectedSessionId } : {};
      // 여기서 고른 색과 밝기는 장치에만 바로 적용하는 일회성 값이다. 프로필이나
      // 작업 모드에 저장하지 않으므로, 다음 작업 모드 변경에서 모드 값으로 돌아간다.
      if (ledOn) {
        // 밝기를 먼저 맞추고 색을 올린다. 색이 켜지는 순간 이미 그 밝기다.
        await controlWled({ action: "BRIGHTNESS", brightness, ...session });
        await controlWled({ action: "SOLID", color, ...session });
      } else {
        await controlWled({ action: "OFF", ...session });
      }
      await wled.refresh(); close(); toast("조명을 적용했어요. 작업 모드를 바꾸면 모드 설정으로 돌아갑니다.");
    } catch (error) { await refresh(); toast(errorText(error)); }
  };

  const chooseMode = async (mode: ActivityMode) => {
    if (!expectedSessionId || !registeredProfileId) { toast("등록 사용자 session에서만 작업 모드를 바꿀 수 있어요."); return; }
    try { await setActivityMode(mode.key, expectedSessionId); await automation.refresh(); close(); toast(`${mode.name}로 변경했어요.`); }
    catch (error) { await refresh(); toast(errorText(error)); }
  };

  const chooseTiltLevel = async (level: number) => {
    try { await setTiltTarget(level); await tilt.refresh(); toast(`${level}단계 틸팅을 요청했어요.`); }
    catch (error) { await tilt.refresh(); toast(errorText(error)); }
  };

  const stopTiltMotion = async () => {
    try { await stopTilt(); await tilt.refresh(); toast("틸팅을 정지했어요."); }
    catch (error) { await tilt.refresh(); toast(errorText(error)); }
  };

  const cards = useMemo(() => [
    { id: "profile" as const, label: "사용자 프로필", value: profile?.name ?? (session?.kind === "ANONYMOUS" ? "게스트" : "프로필 설정"), sub: profile ? "프로필 수정하기" : "새 프로필 등록하기" },
    { id: "height" as const, label: "책상 높이", value: `${currentHeight.toFixed(1)} cm`, sub: `${automationMode === "AUTO" ? "자동" : "수동"} 제어 · 설정 변경`, busy: deskBusyLabel },
    { id: "led" as const, label: "LED 조명", value: ledOn ? "켜짐" : "꺼짐", sub: `#${color}` },
    { id: "mode" as const, label: "현재 모드", value: selectedMode?.name ?? "기본 모드", sub: "작업 환경 변경" },
    { id: "tilt" as const, label: "데스크 틸팅", value: tilt.value?.status === "MOVING" && tilt.value?.targetLevel !== null ? `${tilt.value?.targetLevel}단계 이동 중` : tilt.value?.level !== null ? `${tilt.value?.level}단계` : "준비 중", sub: tilt.value?.detail ?? "기울기 단계 변경", busy: tiltBusyLabel },
  ], [automationMode, color, currentHeight, ledOn, profile, selectedMode?.name, session?.kind, tilt.value]);

  return <div className="smaty-page">
    <header><a className="brand" href="/">▰ <b>SMATY</b></a><div className="head"><nav className="header-diagnostics" aria-label="진단 페이지"><a className="header-link" href="/debug/vision">Vision 진단</a><a className="header-link" href="/debug/voice">AI 스피커 진단</a></nav><span className={deskOnline ? "online" : "online offline"}>● {deskOnline ? "책상 연결됨" : "연결 확인 중"}</span><button className="theme" type="button" onClick={() => setDark((value) => !value)}>☀ <i>{dark ? "●" : "○"}</i> ☾</button></div></header>
    <main><section className="hero"><div><small>MY WORKSPACE</small><h1>안녕하세요, {profile?.name ?? (session?.kind === "ANONYMOUS" ? "게스트" : "사용자")}님.</h1><p>{session ? "오늘도 편안한 환경에서 집중해 보세요." : "카메라가 사용자를 인식하면 개인 설정을 불러옵니다."}</p></div><aside><span>현재 책상 높이</span><b>{currentHeight.toFixed(1)} <small>cm</small></b></aside></section>
      <section className="grid">{cards.map((card, index) => <button key={card.id} type="button" className={`card c${index}`} onClick={() => { if (card.id === "profile") navigate(profile ? `/settings/profiles/${encodeURIComponent(profile.id)}` : "/settings/profiles/new"); else setPanel(card.id); }}><div className="cardtop"><Icon name={card.id} /><span>↗</span></div><small>{card.label}</small><h2>{card.value}</h2>{"busy" in card && card.busy && <em className="busy">{card.busy}</em>}{card.id === "led" && <span className="dot" style={{ background: `#${color}` }} />}{card.id === "tilt" && <div className="steps">{tiltLevels.map((level) => <i key={level} />)}</div>}<p>{card.sub}<b>›</b></p></button>)}<WorkRhythmCard className="c5" profileId={registeredProfileId} profileName={profile?.name ?? null} /></section>
      <section className="assistant-grid" aria-label="AI 스피커 상태와 응답"><AssistantPanel currentSessionId={session?.sessionId ?? null} currentSessionKind={session?.kind ?? null} registeredProfileName={profile?.name ?? null} /></section>
      <section className="diagnostic-links" aria-label="시스템 진단"><div><small>SYSTEM DEBUG</small><h2>실시간 진단 도구</h2><p>음성 상태와 카메라 추론 결과를 운영 장비에서 바로 확인합니다.</p></div><nav><a href="/debug/voice"><b>AI 스피커 디버그</b><span>Wake Word · 마이크 · 응답 상태</span></a><a href="/debug/vision"><b>Vision 디버그</b><span>상·하단 프레임 · 감지 오버레이</span></a></nav></section>
    </main><footer>SMATY <span>나에게 맞춰지는 더 나은 작업 환경</span></footer>
    {notice && <div className="toast" role="status">✓ {notice}</div>}
    {panel && <div className="shade" onMouseDown={close}><section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}><button className="x" type="button" onClick={close} aria-label="닫기">×</button>
      {panel === "height" && <><Title icon="height" eyebrow="DESK HEIGHT" title="높이 및 제어 설정" /><div className="tabs"><button type="button" className={automationMode === "AUTO" ? "on" : ""} onClick={() => void changeControlMode("AUTO")}>자동 제어</button><button type="button" className={automationMode === "MANUAL" ? "on" : ""} onClick={() => void changeControlMode("MANUAL")}>수동 제어</button></div><div className="read"><span>목표 높이</span><b>{targetHeight.toFixed(1)} cm</b></div><input className="range" aria-label="목표 높이" type="range" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.5" value={targetHeight} onChange={(event) => setTargetHeight(Number(event.target.value))} /><div className="twocol"><Field title="앉은 높이"><input type="number" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.1" value={sittingHeight} onChange={(event) => editStoredHeight("sitting", Number(event.target.value))} /></Field><Field title="서있는 높이"><input type="number" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.1" value={standingHeight} onChange={(event) => editStoredHeight("standing", Number(event.target.value))} /></Field></div><p className="modal-note">{automationMode === "MANUAL" ? "수동 제어에서는 앉기·서기 높이를 이번 이동에만 씁니다. 프로필에는 저장하지 않아요." : "자동 제어에서는 앉기·서기 높이를 프로필에 저장해, 자세에 따라 계속 사용합니다."}</p><Primary disabled={!deskOnline} onClick={() => void applyHeight()}>설정 적용하기</Primary></>}
      {panel === "led" && <><Title icon="led" eyebrow="LED LIGHT" title="조명 설정" /><div className="power"><span><b>LED 전원</b><small>{ledOn ? "조명이 켜져 있어요" : "조명이 꺼져 있어요"}</small></span><button type="button" className={ledOn ? "on" : ""} onClick={() => setLedOn((value) => !value)} aria-label="LED 전원"><i /></button></div><Field title="모든 색상에서 선택"><div className="color"><input type="color" value={`#${color}`} onChange={(event) => setColor(event.target.value.slice(1).toUpperCase())} /><b>#{color}</b></div></Field><div className="swatches">{["765CF6", "50C59D", "F5B544", "F06C7E", "4D9CF0", "FFFFFF"].map((item) => <button type="button" key={item} aria-label={`#${item}`} style={{ background: `#${item}` }} onClick={() => setColor(item)} />)}</div><Field title="밝기"><div className="brightness"><input type="range" min={LED_BRIGHTNESS_MIN} max={LED_BRIGHTNESS_MAX} step="1" value={brightness} onChange={(event) => setBrightness(Number(event.target.value))} /><b>{brightness}</b></div></Field><p className="modal-note">지금 조명에만 적용하는 일회성 설정이에요. 작업 모드를 바꾸면 그 모드에 저장된 색과 밝기로 돌아갑니다.</p><Primary onClick={() => void applyLed()}>조명에 적용하기</Primary></>}
      {panel === "mode" && <><Title icon="mode" eyebrow="WORK MODE" title="모드 선택" /><div className="options">{modes.length ? modes.map((mode) => <button type="button" key={mode.key} className={selectedMode?.key === mode.key ? "on" : ""} onClick={() => void chooseMode(mode)}><Icon name="mode" /><span><b>{mode.name}{mode.ledColor && <i className="swatch" style={{ background: `#${mode.ledColor}` }} />}</b><small>{modeSubtitle(mode)}</small></span>{selectedMode?.key === mode.key && "✓"}</button>) : <p className="modal-note">등록 사용자가 인식되면 저장한 작업 모드를 선택할 수 있어요.</p>}</div></>}
      {panel === "tilt" && <><Title icon="tilt" eyebrow="DESK TILTING" title="틸팅 단계 선택" /><p className="desc">{tilt.value?.detail ?? "틸팅 제어 장치를 확인하고 있습니다."}</p><div className="tilts">{tiltLevels.map((level, index) => <button type="button" key={level} className={tilt.value?.level === level ? "on" : ""} disabled={!tiltCanMove} onClick={() => void chooseTiltLevel(level)}><span style={{ transform: `rotate(${-index * 4}deg)` }}>━</span><b>{level}단계</b><small>{level === tilt.value?.level ? "현재 단계" : "이동"}</small></button>)}</div>{tilt.value?.status === "MOVING" && <Primary onClick={() => void stopTiltMotion()}>틸팅 정지</Primary>}{!tiltLevels.length && <p className="modal-note">단계 설정을 불러오지 못했습니다.</p>}</>}
    </section></div>}
  </div>;
}

function Title({ icon, eyebrow, title }: { icon: Exclude<Panel, null>; eyebrow: string; title: string }) { return <div className="title"><Icon name={icon} /><span><small>{eyebrow}</small><h2>{title}</h2></span></div>; }
function Field({ title, children }: { title: string; children: React.ReactNode }) { return <label className="field"><span>{title}</span>{children}</label>; }
function Primary(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button {...props} type="button" className="primary" />; }

function Screen() {
  const pathname = usePathname();
  if (pathname.startsWith("/settings/profiles")) return <ProfileSettings pathname={pathname} />;
  if (pathname === "/debug/vision") return <DebugPanel />;
  if (pathname === "/reports/work-rhythm") return <WorkRhythm />;
  return <SmatyDashboard />;
}

export default function App() {
  // 키보드는 화면 위에 겹쳐 뜨므로 라우팅 밖에 두어 어느 화면에서든 살아 있게 한다.
  return <><Screen /><OnScreenKeyboard /></>;
}
