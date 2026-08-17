import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, controlWled, getAutomationStatus, getCurrentUser, getDeskStatus, getProfile, getVisionStatus, getWledStatus, listActivityModes, setActivityMode, setControlMode, type ActivityMode, type AutomationStatus } from "./api/dashboard";
import { DebugPanel } from "./features/debug/DebugPanel";
import { DeskPanel } from "./features/desk/DeskPanel";
import { ProfileSettings } from "./features/profiles/ProfileSettings";
import { useSnapshotPoll, type Polled } from "./hooks/useSnapshotPoll";
import { navigate, usePathname } from "./routes";
import "./styles.css";

const age = (at: number | null) => at === null ? "아직 성공 snapshot 없음" : `${Math.max(0, Math.round((Date.now() - at) / 1000))}초 전`;
const commandMessage = (error: unknown) => error instanceof ApiError ? error.status === 422 ? "입력값 또는 범위를 확인해 주세요." : error.status === 503 ? "관련 장치 또는 서비스가 아직 준비되지 않았습니다." : error.message : error instanceof Error ? `네트워크 오류: ${error.message}` : "요청을 처리하지 못했습니다.";
const statusText = (item: Polled<unknown>) => item.error ? `오류 · ${item.error}` : `마지막 성공 ${age(item.lastSuccessAt)}`;
const wledDescription = (snapshot: ReturnType<typeof getWledStatus> extends Promise<infer T> ? T | null : never) => {
  if (!snapshot || snapshot.status === "UNKNOWN") return "조명 상태를 아직 확인하지 못했습니다.";
  if (snapshot.status === "DISABLED") return "조명 기능이 비활성화되어 있습니다.";
  if (snapshot.status === "ERROR") return "조명 상태를 읽는 중 오류가 있습니다.";
  if (snapshot.on === false || snapshot.mode === "OFF") return "조명이 꺼져 있습니다.";
  if (snapshot.mode === "EFFECT") return snapshot.effectName ? `효과 ${snapshot.effectName} 실행 중` : "효과 실행 중";
  if (snapshot.mode === "MIXED") return "여러 조명 segment 상태입니다.";
  return snapshot.color ? `현재 색상 #${snapshot.color}` : "현재 조명 상태입니다.";
};

function Dashboard() {
  const current = useSnapshotPoll(useCallback((signal) => getCurrentUser(signal), []), 1000);
  const vision = useSnapshotPoll(useCallback((signal) => getVisionStatus(signal), []), 1000);
  const automation = useSnapshotPoll(useCallback((signal) => getAutomationStatus(signal), []), 1000);
  const desk = useSnapshotPoll(useCallback((signal) => getDeskStatus(signal), []), 750);
  const wled = useSnapshotPoll(useCallback((signal) => getWledStatus(signal), []), 2000);
  const [profileName, setProfileName] = useState<string | null>(null);
  const [modes, setModes] = useState<ActivityMode[]>([]);
  const [modeError, setModeError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const modeSequence = useRef(0);
  const sessionGeneration = useRef(0);
  const session = current.value?.session ?? null;
  const expectedSessionId = session?.sessionId ?? null;
  const registeredProfileId = session?.kind === "REGISTERED" ? session.profileId : null;
  const renderedSessionId = useRef(expectedSessionId);
  if (renderedSessionId.current !== expectedSessionId) {
    renderedSessionId.current = expectedSessionId;
    ++sessionGeneration.current;
  }

  useEffect(() => {
    const mine = ++modeSequence.current;
    setProfileName(null); setModes([]); setModeError(null); setNotice(null);
    if (!expectedSessionId || !registeredProfileId) return;
    const controller = new AbortController();
    void Promise.all([getProfile(registeredProfileId, controller.signal), listActivityModes(registeredProfileId, controller.signal)])
      .then(([profile, availableModes]) => {
        if (mine !== modeSequence.current || controller.signal.aborted) return;
        setProfileName(profile.name); setModes(availableModes);
      })
      .catch((error) => {
        if (mine !== modeSequence.current || controller.signal.aborted) return;
        setModeError(error instanceof Error ? error.message : "프로필 작업 모드를 읽지 못했습니다.");
      });
    return () => { controller.abort(); ++modeSequence.current; };
  }, [expectedSessionId, registeredProfileId]);

  const resync = async () => {
    await Promise.allSettled([current.refresh(), automation.refresh()]);
  };
  const mode = async (next: "AUTO" | "MANUAL") => {
    if (!expectedSessionId) return;
    const generation = sessionGeneration.current;
    try {
      await setControlMode(next, expectedSessionId);
      await automation.refresh();
      if (generation === sessionGeneration.current) setNotice(`${next === "AUTO" ? "AUTO" : "MANUAL"} 전환 요청을 반영했습니다.`);
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      if (error instanceof ApiError && error.status === 409) {
        await resync();
        if (generation === sessionGeneration.current) setNotice("사용자 session이 변경되었습니다. 성공으로 처리하지 않고 최신 상태를 다시 읽습니다.");
      } else setNotice(commandMessage(error));
    }
  };
  const activity = async (key: string) => {
    if (!expectedSessionId) return;
    const generation = sessionGeneration.current;
    try {
      const result = await setActivityMode(key, expectedSessionId);
      await automation.refresh();
      if (generation === sessionGeneration.current) setNotice(result.controlMode === "MANUAL" ? "작업 모드를 바꿨습니다. MANUAL에서는 LED만 즉시 바뀌며 책상은 움직이지 않습니다." : "작업 모드를 바꿨습니다. AUTO에서는 서버가 안전 조건에 따라 목표를 재평가합니다.");
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      if (error instanceof ApiError && error.status === 409) {
        await resync();
        if (generation === sessionGeneration.current) setNotice("사용자 session이 변경되었습니다. 성공으로 처리하지 않고 최신 상태를 다시 읽습니다.");
      } else setNotice(commandMessage(error));
    }
  };
  const led = async () => {
    try { await controlWled({ action: "OFF" }); await wled.refresh(); setNotice("현재 서버 계약의 수동 LED 끄기 요청을 보냈습니다. 저장 profile 색상은 변경하지 않습니다."); }
    catch (error) { setNotice(commandMessage(error)); }
  };
  const automationValue: AutomationStatus | null = automation.value;
  const manual = automationValue?.controlMode === "MANUAL";
  const relay = desk.value?.relay;
  const canControl = !desk.error && desk.value?.height.status === "ONLINE" && Boolean(relay?.event) && relay?.event !== "offline" && relay?.event !== "rejected" && Boolean(relay?.receivedAt) && !relay?.lastError;

  return <><header className="site-header"><a className="logo" href="/">SMART DESK</a><nav><button onClick={() => navigate("/settings/profiles")}>프로필 설정</button><button onClick={() => navigate("/debug/vision")}>Vision 진단</button></nav></header><main className="dashboard-main"><section className="page-heading"><div><p className="eyebrow">CURRENT DASHBOARD</p><h1>메인 대시보드</h1><p>서버가 결정한 현재 사용자와 기능별 snapshot입니다.</p></div></section>{notice && <p className="connection-error" role="status">{notice}</p>}<section className="dashboard-grid"><article className="card"><p className="card-label">CURRENT USER</p><h2>{session ? session.kind === "ANONYMOUS" ? "게스트" : profileName ?? "등록 사용자 확인 중" : "사용자 없음"}</h2><p className="control-note">{session ? `session ${session.sessionId} · ${session.kind}` : "재실 안정화 뒤 등록 사용자 또는 게스트 session이 시작됩니다."}</p><p className="control-note">{statusText(current)}</p></article><article className="card"><p className="card-label">VISION</p><h2>{vision.value?.posture.status ?? "UNKNOWN"}</h2><p className="control-note">재실 {vision.value?.presence.status ?? "UNKNOWN"} (raw {vision.value?.presence.rawStatus ?? "UNKNOWN"}) · 신원 {vision.value?.identity.status ?? "UNKNOWN"}</p><p className="control-note">{statusText(vision)} · {vision.value?.association.reasonCodes.join(", ") || "association 정상"}</p></article><article className="card"><p className="card-label">CONTROL MODE · 제어 방식</p><h2>{automationValue?.controlMode ?? "없음"}</h2><div className="led-actions"><button className="previous-button" disabled={!expectedSessionId} onClick={() => void mode("AUTO")}>AUTO</button><button className="previous-button" disabled={!expectedSessionId} onClick={() => void mode("MANUAL")}>MANUAL</button></div><p className="control-note">{statusText(automation)}</p></article><article className="card"><p className="card-label">ACTIVITY MODE · 작업 모드</p><h2>{automationValue?.activityMode?.name ?? (session?.kind === "ANONYMOUS" ? "없음 (게스트)" : "없음")}</h2>{registeredProfileId ? <><label className="activity-picker">작업 모드<select value={automationValue?.activityMode?.key ?? ""} disabled={!expectedSessionId || modes.length === 0} onChange={(event) => void activity(event.target.value)}><option value="" disabled>선택</option>{modes.map((item) => <option key={item.key} value={item.key}>{item.kind === "DEFAULT" ? "기본 · " : "사용자 · "}{item.name}</option>)}</select></label><p className="control-note">{manual ? "MANUAL에서 이 선택은 LED만 즉시 바뀌며 책상은 이동하지 않습니다." : "AUTO에서는 서버가 안전 조건을 확인해 목표 높이를 재평가합니다."}</p>{modeError && <p className="inline-error">{modeError}</p>}</> : <p className="control-note">게스트/사용자 없음에서는 개인 mode와 profile 저장값을 사용하지 않습니다. 게스트 높이 정책은 75/110cm입니다.</p>}</article></section><section className="card dashboard-section"><p className="card-label">AUTOMATION</p><h2>{automationValue?.state ?? "확인 중"}</h2><p className="control-note">차단: {automationValue?.blockedReasonCodes.join(", ") || "없음"} · target intent: {automationValue?.intentSource ?? "없음"} · transition: {automationValue?.lastTransitionReason ?? "확인 중"}</p></section><DeskPanel status={desk.value} canControl={canControl} controlError={desk.error} onStatus={() => void desk.refresh()} onError={setNotice} /><section className="card dashboard-section"><p className="card-label">WLED</p><h2>{wled.value?.status ?? "확인 중"}</h2><p className="control-note">{wledDescription(wled.value)} · {statusText(wled)}</p><button className="previous-button" disabled={wled.value?.status === "DISABLED"} onClick={() => void led()}>조명 끄기</button></section><section className="card dashboard-section"><p className="card-label">VOICE / AI</p><h2>Assistant API 통합 전</h2><p className="control-note">연결 상태나 응답을 표시하지 않습니다. Assistant polling은 후속 작업 범위입니다.</p></section><p className="control-note">Desk 상태 {desk.value?.state ?? "확인 중"} · {statusText(desk)}. API 접수와 실제 이동/완료는 Desk snapshot으로 구분합니다.</p></main></>;
}

export default function App() { const pathname = usePathname(); if (pathname.startsWith("/settings/profiles")) return <ProfileSettings pathname={pathname} />; if (pathname === "/debug/vision") return <DebugPanel />; return <Dashboard />; }
