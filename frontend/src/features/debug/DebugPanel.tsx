import { useCallback } from "react";
import { getAutomationStatus, getCurrentUser, getVisionStatus } from "../../api/dashboard";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";
import { navigate } from "../../routes";

const value = (item: unknown) => item === null || item === undefined || item === "" ? "--" : Array.isArray(item) ? item.join(", ") || "없음" : String(item);
const cameraPaths: Record<string, string> = { upper: "user-cam", lower: "bottom-cam" };

function webRtcPreviewUrl(path: string) {
  const url = new URL(`${window.location.protocol}//${window.location.hostname}:8889/${path}`);
  url.searchParams.set("controls", "false");
  url.searchParams.set("muted", "true");
  url.searchParams.set("autoplay", "true");
  url.searchParams.set("playsInline", "true");
  return url.toString();
}

export function DebugPanel() {
  const vision = useSnapshotPoll(useCallback((signal) => getVisionStatus(signal), []), 1000);
  const user = useSnapshotPoll(useCallback((signal) => getCurrentUser(signal), []), 1000);
  const automation = useSnapshotPoll(useCallback((signal) => getAutomationStatus(signal), []), 1000);
  const cameras = vision.value?.cameras ?? {};

  return <>
    <header className="site-header"><a className="logo" href="/">SMART DESK</a><button onClick={() => navigate("/")}>대시보드로</button></header>
    <main className="debug-main">
      <section className="debug-heading"><div><p className="eyebrow">VISION DEBUG</p><h1>카메라·상태 전이 진단</h1><p>원본 영상은 브라우저가 WebRTC로 직접 재생하며 서버에 저장하지 않습니다.</p></div></section>
      <section className="preview-grid">{[["upper", "상단 카메라"], ["lower", "하단 카메라"]].map(([key, name]) => {
        const camera = cameras[key];
        return <article className="video-panel" key={key}>
          <h2>{name}</h2>
          <div className="debug-preview"><iframe src={webRtcPreviewUrl(cameraPaths[key])} title={`${name} WebRTC 미리보기`} allow="autoplay; fullscreen; picture-in-picture" /></div>
          <p>상태 {value(camera?.status)} · frame age {camera?.ageSeconds === null || camera?.ageSeconds === undefined ? "--" : `${camera.ageSeconds.toFixed(1)}초`} · 오류 {value(camera?.error)}</p>
        </article>;
      })}</section>
      <section className="summary-grid">{[["raw 재실", vision.value?.presence.rawStatus], ["stable 재실", vision.value?.presence.status], ["raw 자세", vision.value?.posture.rawStatus], ["stable 자세", vision.value?.posture.status], ["신원", vision.value?.identity.status], ["association", vision.value?.association.usable], ["현재 session", user.value?.session?.sessionId], ["제어 방식", automation.value?.controlMode], ["작업 모드", automation.value?.activityMode?.name], ["자동화", automation.value?.state]].map(([label, item]) => <article key={String(label)}><span>{label}</span><strong>{value(item)}</strong></article>)}</section>
      <section className="detail-grid"><article className="detail-card"><h2>관측·귀속</h2><p>상단/하단 count: {value(vision.value?.presence.upperCount)} / {value(vision.value?.presence.lowerCount)}</p><p>association reasons: {value(vision.value?.association.reasonCodes)}</p><p>identity profile: {value(vision.value?.identity.profileId)}</p></article><article className="detail-card"><h2>자동화 전이</h2><p>차단: {value(automation.value?.blockedReasonCodes)}</p><p>target intent: {value(automation.value?.intentSource)} · target: {value(automation.value?.targetHeightCm)}</p><p>transition: {value(automation.value?.lastTransitionReason)} / {value(automation.value?.lastTransitionSource)}</p></article></section>
      {(vision.error || user.error || automation.error) && <p className="connection-error">진단 polling 오류: {[vision.error, user.error, automation.error].filter(Boolean).join(" · ")}</p>}
    </main>
  </>;
}
