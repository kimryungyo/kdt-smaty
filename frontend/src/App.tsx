import { useCallback, useEffect, useRef, useState } from "react";

import { type DeskStatus, type Profile, getDeskStatus } from "./api/dashboard";
import { DeskPanel } from "./features/desk/DeskPanel";
import { DebugPanel } from "./features/debug/DebugPanel";
import { HeightSetup, ProfileBasics, ProfilePicker } from "./features/profiles/ProfilesPanel";
import { LegacyStyle } from "./legacy/LegacyStyle";
import dashboardCss from "./legacy/dashboard.css?raw";

type Page = "picker" | "dashboard" | "basics" | "height-setup" | "debug";

export default function App() {
  const [deskStatus, setDeskStatus] = useState<DeskStatus | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<Profile | null>(null);
  const [page, setPage] = useState<Page>("picker");
  const [draftName, setDraftName] = useState("");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const requestInFlight = useRef(false);

  useEffect(() => {
    if (page !== "dashboard") return;
    let active = true;
    const refresh = async () => {
      if (requestInFlight.current) return;
      requestInFlight.current = true;
      try {
        const status = await getDeskStatus();
        if (active) { setDeskStatus(status); setConnectionError(null); }
      } catch (error) {
        if (active) setConnectionError(error instanceof Error ? error.message : "서버 상태를 확인하지 못했습니다.");
      } finally {
        requestInFlight.current = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 750);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [page]);

  const updateStatus = useCallback((status: DeskStatus) => { setDeskStatus(status); setConnectionError(null); }, []);
  const reportError = useCallback((message: string) => setConnectionError(message), []);
  const canControl =
    connectionError === null &&
    deskStatus?.height.status === "ONLINE" &&
    deskStatus.relay.event !== null &&
    deskStatus.relay.event !== "offline" &&
    deskStatus.relay.event !== "rejected" &&
    deskStatus.relay.receivedAt !== null &&
    deskStatus.relay.lastError === null &&
    !["height_waiting", "height_not_ready", "height_stale"].includes(deskStatus.relay.code ?? "");

  if (page === "picker") {
    return <><header className="site-header"><a className="logo" href="/" aria-label="SMART DESK 홈"><span className="logo-mark" aria-hidden="true" />SMART DESK</a></header><ProfilePicker onSelect={(profile) => { setSelectedProfile(profile); setDraftName(profile.name); setPage("dashboard"); }} onCreate={() => { setSelectedProfile(null); setDraftName(""); setPage("basics"); }} /></>;
  }

  if (page === "basics") {
    return <><header className="site-header"><a className="logo" href="/" aria-label="SMART DESK 홈"><span className="logo-mark" aria-hidden="true" />SMART DESK</a><span className="progress">1 / 2</span></header><ProfileBasics name={draftName} onNameChange={setDraftName} onNext={() => setPage("height-setup")} /></>;
  }

  if (page === "height-setup") {
    return <><header className="site-header"><a className="logo" href="/" aria-label="SMART DESK 홈"><span className="logo-mark" aria-hidden="true" />SMART DESK</a><span className="progress">2 / 2</span></header><HeightSetup profile={selectedProfile} name={draftName} onSaved={(profile) => { setSelectedProfile(profile); setPage("dashboard"); }} onPrevious={() => setPage("basics")} /></>;
  }

  if (page === "debug") {
    return <><header className="site-header"><a className="logo" href="/" aria-label="SMART DESK 홈"><span className="logo-mark" aria-hidden="true" />SMART DESK</a></header><DebugPanel onBack={() => setPage("dashboard")} /></>;
  }

  return (
    <>
      <LegacyStyle css={dashboardCss} />
      <header className="site-header">
        <a className="logo" href="/" aria-label="SMART DESK 홈"><span className="logo-mark" aria-hidden="true" />SMART DESK</a>
        <div className={`live-status ${connectionError ? "offline" : ""}`}><span /><b>{connectionError ? "SYSTEM CHECK" : "SYSTEM ONLINE"}</b></div>
      </header>
      <main>
        <section className="page-heading">
          <div><p className="eyebrow">OVERVIEW</p><h1>메인 대시보드</h1><p>현재 모션데스크의 상태를 확인할 수 있습니다.</p></div>
          <p className="current-time">{deskStatus ? new Date(deskStatus.updatedAt).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "상태 확인 중"}</p>
        </section>
        {connectionError && <p className="connection-error" role="alert">Dashboard 연결 오류: {connectionError} 마지막 표시값은 현재 상태가 아닐 수 있습니다.</p>}
        <section className="dashboard-grid">
          <article className="card profile-card"><div className="card-header"><div><p className="card-label">USER</p><h2>사용자 정보</h2></div><span className="card-number">01</span></div><div className="profile-content"><div className="avatar" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 12.4a4.4 4.4 0 1 0 0-8.8 4.4 4.4 0 0 0 0 8.8Zm0 1.8c-5.15 0-8.7 2.6-8.7 5.25 0 .53.43.95.95.95h15.5c.52 0 .95-.42.95-.95 0-2.65-3.55-5.25-8.7-5.25Z" /></svg></div><div><strong>{selectedProfile?.name ?? "사용자"}</strong><p><span>--</span> cm</p></div></div></article>
          <article className="card posture-card"><div className="card-header"><div><p className="card-label">POSTURE</p><h2>현재 사용자 상태</h2></div><span className="recognition"><i /> <b>확인 중</b></span></div><div className="posture-content"><div className="posture-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="2.2" /><path d="M12 8.2v5.3m0 0H8.5m3.5 0 3.2 3.2M8.5 13.5v5M5.5 18.5h6" /></svg></div><div><span>현재 자세</span><strong>확인 중</strong></div></div><p className="posture-note">Vision 상태를 기다리고 있습니다.</p></article>
          <article className="card height-card"><div className="card-header"><div><p className="card-label">HEIGHT PRESET</p><h2>저장된 높이</h2></div><span className="card-number">03</span></div><div className="height-list"><div><span><i className="sitting-dot" /> 앉은 자세</span><strong>{selectedProfile?.sittingHeightCm.toFixed(1) ?? "--.-"}<small>cm</small></strong></div><div><span><i className="standing-dot" /> 서 있는 자세</span><strong>{selectedProfile?.standingHeightCm.toFixed(1) ?? "--.-"}<small>cm</small></strong></div></div></article>
          <article className="card automation-card"><div className="card-header"><div><p className="card-label">AUTOMATION</p><h2>자동 높이 조절</h2></div><span className="card-number">04</span></div><div className="automation-content"><div><span>자동 조절 상태</span><strong>ON</strong></div><label className="switch" aria-label="자동 높이 조절"><input type="checkbox" checked disabled readOnly /><span /></label></div><p>자세 변화 감지 후 <strong>5초</strong> 뒤 높이를 조절합니다.</p></article>
        </section>
        <DeskPanel status={deskStatus} profile={selectedProfile} canControl={canControl} onStatus={updateStatus} onError={reportError} />
        <section className="led-grid" aria-label="LED 조명 제어"><article className="card led-card"><div className="card-header"><div><p className="card-label">LED</p><h2>LED 조명 색상</h2></div><span className="led-mode-badge">자동</span></div><div className="led-content"><label className="led-swatch"><input type="color" value={`#${selectedProfile?.ledColor ?? "0080ff"}`} disabled readOnly /></label><div className="led-actions"><button type="button" className="complete-button" disabled>이 색상 적용</button><button type="button" className="previous-button" disabled>인식 색상으로 새로고침</button></div></div><p className="control-note">기본 색상은 흰색입니다. 색상을 적용하면 즉시 바뀌고 현재 프로필에 저장되어, 다음에 카메라가 이 사용자를 인식할 때 자동으로 이 색으로 바뀝니다. WLED 장치가 연결되어 있지 않으면 화면에는 반영되지만 조명은 바뀌지 않습니다.</p><p className="status-message" role="status" /></article></section>
        <nav className="dashboard-actions" aria-label="설정 바로가기"><a href="#profiles" onClick={(event) => { event.preventDefault(); setPage("picker"); }}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M9 12h11M4 17h16" /></svg>프로필 전환</a><a href="#profile-edit" onClick={(event) => { event.preventDefault(); setDraftName(selectedProfile?.name ?? ""); setPage("basics"); }}><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3" /><path d="M5.5 20c.5-4 2.8-6 6.5-6s6 2 6.5 6" /></svg>프로필 수정</a><a className="primary-action" href="#height-settings" onClick={(event) => { event.preventDefault(); setPage("height-setup"); }}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6m8 4v6" /></svg>높이 설정</a><a href="#vision-debug" onClick={(event) => { event.preventDefault(); setPage("debug"); }}>Vision 디버그</a></nav>
      </main>
    </>
  );
}
