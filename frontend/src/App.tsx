import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  controlWled,
  getAutomationStatus,
  getCurrentUser,
  getDeskStatus,
  getProfile,
  getTiltStatus,
  getWledStatus,
  listActivityModes,
  setActivityMode,
  setControlMode,
  setTarget,
  updateProfile,
  type ActivityMode,
  type Profile,
} from "./api/dashboard";
import { DebugPanel } from "./features/debug/DebugPanel";
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

const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 409) return "현재 사용자 session이 변경되었습니다. 최신 상태를 다시 확인해 주세요.";
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
  const [ledOn, setLedOn] = useState(false);
  const [color, setColor] = useState("FFFFFF");
  const [notice, setNotice] = useState("");
  const session = current.value?.session ?? null;
  const registeredProfileId = session?.kind === "REGISTERED" ? session.profileId : null;
  const expectedSessionId = session?.sessionId ?? null;
  const selectedMode = automation.value?.activityMode ?? null;
  const automationMode = automation.value?.controlMode ?? null;

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
    const snapshot = wled.value;
    // 조명 패널을 여는 동안에는 고르는 중인 값을 장치 상태로 덮어쓰지 않는다.
    if (!snapshot || panel === "led") return;
    setLedOn(snapshot.on === true && snapshot.mode !== "OFF");
    if (snapshot.color) setColor(snapshot.color);
  }, [panel, wled.value]);

  const refresh = async () => { await Promise.allSettled([current.refresh(), desk.refresh(), wled.refresh(), automation.refresh()]); };
  const close = () => setPanel(null);
  const currentHeight = desk.value?.height.heightCm ?? targetHeight;
  const deskOnline = desk.value?.height.status === "ONLINE" && Boolean(desk.value?.relay.receivedAt) && !desk.value?.relay.lastError;

  const applyHeight = async () => {
    try {
      if (profile) setProfile(await updateProfile(profile.id, { sittingHeightCm: sittingHeight, standingHeightCm: standingHeight }));
      if (automationMode === "MANUAL" || !expectedSessionId) {
        await setTarget(targetHeight);
        await desk.refresh();
        toast("목표 높이 이동을 요청했어요.");
      } else {
        toast("AUTO 모드에서는 감지된 자세에 따라 서버가 높이를 결정합니다. 프로필 높이만 저장했어요.");
      }
      close();
    } catch (error) { toast(errorText(error)); }
  };

  const changeControlMode = async (next: "AUTO" | "MANUAL") => {
    if (!expectedSessionId) { toast("현재 사용자 session이 있어야 제어 방식을 바꿀 수 있어요."); return; }
    try { await setControlMode(next, expectedSessionId); await automation.refresh(); }
    catch (error) { await refresh(); toast(errorText(error)); }
  };

  const applyLed = async () => {
    try {
      const command = ledOn ? { action: "SOLID" as const, color, ...(expectedSessionId ? { expectedSessionId } : {}) } : { action: "OFF" as const, ...(expectedSessionId ? { expectedSessionId } : {}) };
      // 여기서 고른 색은 장치에만 바로 적용하는 일회성 값이다. 프로필이나 작업
      // 모드에 저장하지 않으므로, 다음 작업 모드 변경에서 모드 색으로 돌아간다.
      await controlWled(command);
      await wled.refresh(); close(); toast("조명을 적용했어요. 작업 모드를 바꾸면 모드 색으로 돌아갑니다.");
    } catch (error) { await refresh(); toast(errorText(error)); }
  };

  const chooseMode = async (mode: ActivityMode) => {
    if (!expectedSessionId || !registeredProfileId) { toast("등록 사용자 session에서만 작업 모드를 바꿀 수 있어요."); return; }
    try { await setActivityMode(mode.key, expectedSessionId); await automation.refresh(); close(); toast(`${mode.name}로 변경했어요.`); }
    catch (error) { await refresh(); toast(errorText(error)); }
  };

  const cards = useMemo(() => [
    { id: "profile" as const, label: "사용자 프로필", value: profile?.name ?? (session?.kind === "ANONYMOUS" ? "게스트" : "프로필 설정"), sub: profile ? "프로필 수정하기" : "새 프로필 등록하기" },
    { id: "height" as const, label: "책상 높이", value: `${currentHeight.toFixed(1)} cm`, sub: `${automationMode === "AUTO" ? "자동" : "수동"} 제어 · 설정 변경` },
    { id: "led" as const, label: "LED 조명", value: ledOn ? "켜짐" : "꺼짐", sub: `#${color}` },
    { id: "mode" as const, label: "현재 모드", value: selectedMode?.name ?? "기본 모드", sub: "작업 환경 변경" },
    { id: "tilt" as const, label: "데스크 틸팅", value: tilt.value?.status === "ONLINE" && tilt.value.level !== null ? `${tilt.value.level}단계` : "준비 중", sub: tilt.value?.detail ?? "기울기 단계 변경" },
  ], [automationMode, color, currentHeight, ledOn, profile, selectedMode?.name, session?.kind, tilt.value]);

  return <div className="smaty-page">
    <header><a className="brand" href="/">▰ <b>SMATY</b></a><div className="head"><span className={deskOnline ? "online" : "online offline"}>● {deskOnline ? "책상 연결됨" : "연결 확인 중"}</span><button className="theme" type="button" onClick={() => setDark((value) => !value)}>☀ <i>{dark ? "●" : "○"}</i> ☾</button></div></header>
    <main><section className="hero"><div><small>MY WORKSPACE</small><h1>안녕하세요, {profile?.name ?? (session?.kind === "ANONYMOUS" ? "게스트" : "사용자")}님.</h1><p>{session ? "오늘도 편안한 환경에서 집중해 보세요." : "카메라가 사용자를 인식하면 개인 설정을 불러옵니다."}</p></div><aside><span>현재 책상 높이</span><b>{currentHeight.toFixed(1)} <small>cm</small></b></aside></section>
      <section className="grid">{cards.map((card, index) => <button key={card.id} type="button" className={`card c${index}`} onClick={() => { if (card.id === "profile") navigate(profile ? `/settings/profiles/${encodeURIComponent(profile.id)}` : "/settings/profiles/new"); else setPanel(card.id); }}><div className="cardtop"><Icon name={card.id} /><span>↗</span></div><small>{card.label}</small><h2>{card.value}</h2>{card.id === "led" && <span className="dot" style={{ background: `#${color}` }} />}{card.id === "tilt" && <div className="steps">{[0, 1, 2, 3, 4, 5].map((item) => <i key={item} />)}</div>}<p>{card.sub}<b>›</b></p></button>)}<WorkRhythmCard className="c5" /></section>
    </main><footer>SMATY <span>나에게 맞춰지는 더 나은 작업 환경</span></footer>
    {notice && <div className="toast" role="status">✓ {notice}</div>}
    {panel && <div className="shade" onMouseDown={close}><section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}><button className="x" type="button" onClick={close} aria-label="닫기">×</button>
      {panel === "height" && <><Title icon="height" eyebrow="DESK HEIGHT" title="높이 및 제어 설정" /><div className="tabs"><button type="button" className={automationMode === "AUTO" ? "on" : ""} onClick={() => void changeControlMode("AUTO")}>자동 제어</button><button type="button" className={automationMode === "MANUAL" ? "on" : ""} onClick={() => void changeControlMode("MANUAL")}>수동 제어</button></div><div className="read"><span>목표 높이</span><b>{targetHeight.toFixed(1)} cm</b></div><input className="range" aria-label="목표 높이" type="range" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.5" value={targetHeight} onChange={(event) => setTargetHeight(Number(event.target.value))} /><div className="twocol"><Field title="앉은 높이"><input type="number" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.1" value={sittingHeight} onChange={(event) => setSittingHeight(Number(event.target.value))} /></Field><Field title="서있는 높이"><input type="number" min={MIN_HEIGHT} max={MAX_HEIGHT} step="0.1" value={standingHeight} onChange={(event) => setStandingHeight(Number(event.target.value))} /></Field></div><Primary disabled={!deskOnline} onClick={() => void applyHeight()}>설정 적용하기</Primary></>}
      {panel === "led" && <><Title icon="led" eyebrow="LED LIGHT" title="조명 설정" /><div className="power"><span><b>LED 전원</b><small>{ledOn ? "조명이 켜져 있어요" : "조명이 꺼져 있어요"}</small></span><button type="button" className={ledOn ? "on" : ""} onClick={() => setLedOn((value) => !value)} aria-label="LED 전원"><i /></button></div><Field title="모든 색상에서 선택"><div className="color"><input type="color" value={`#${color}`} onChange={(event) => setColor(event.target.value.slice(1).toUpperCase())} /><b>#{color}</b></div></Field><div className="swatches">{["765CF6", "50C59D", "F5B544", "F06C7E", "4D9CF0", "FFFFFF"].map((item) => <button type="button" key={item} aria-label={`#${item}`} style={{ background: `#${item}` }} onClick={() => setColor(item)} />)}</div><p className="modal-note">지금 조명에만 적용하는 일회성 설정이에요. 작업 모드를 바꾸면 그 모드에 저장된 색으로 돌아갑니다.</p><Primary onClick={() => void applyLed()}>조명에 적용하기</Primary></>}
      {panel === "mode" && <><Title icon="mode" eyebrow="WORK MODE" title="모드 선택" /><div className="options">{modes.length ? modes.map((mode) => <button type="button" key={mode.key} className={selectedMode?.key === mode.key ? "on" : ""} onClick={() => void chooseMode(mode)}><Icon name="mode" /><span><b>{mode.name}{mode.ledColor && <i className="swatch" style={{ background: `#${mode.ledColor}` }} />}</b><small>{modeSubtitle(mode)}</small></span>{selectedMode?.key === mode.key && "✓"}</button>) : <p className="modal-note">등록 사용자가 인식되면 저장한 작업 모드를 선택할 수 있어요.</p>}</div></>}
      {panel === "tilt" && <><Title icon="tilt" eyebrow="DESK TILTING" title="틸팅 단계 선택" /><p className="desc">{tilt.value?.detail ?? "틸팅 제어 장치를 확인하고 있습니다."} 단계 제어는 준비 후 이 화면에서 바로 사용할 수 있어요.</p><div className="tilts six">{["틸팅 없음 · 수평", "낮게", "약간 낮게", "편안하게", "높게", "아주 높게"].map((text, index) => <button type="button" key={text} disabled><span style={{ transform: `rotate(${-index * 4}deg)` }}>━</span><b>{index}단계</b><small>{text}</small></button>)}</div></>}
    </section></div>}
  </div>;
}

function Title({ icon, eyebrow, title }: { icon: Exclude<Panel, null>; eyebrow: string; title: string }) { return <div className="title"><Icon name={icon} /><span><small>{eyebrow}</small><h2>{title}</h2></span></div>; }
function Field({ title, children }: { title: string; children: React.ReactNode }) { return <label className="field"><span>{title}</span>{children}</label>; }
function Primary(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button {...props} type="button" className="primary" />; }

export default function App() {
  const pathname = usePathname();
  if (pathname.startsWith("/settings/profiles")) return <ProfileSettings pathname={pathname} />;
  if (pathname === "/debug/vision") return <DebugPanel />;
  if (pathname === "/reports/work-rhythm") return <WorkRhythm />;
  return <SmatyDashboard />;
}
