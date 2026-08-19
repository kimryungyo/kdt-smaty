import { useCallback, useEffect, useRef } from "react";
import {
  getAutomationStatus,
  getCurrentUser,
  getVisionDebug,
  getVisionStatus,
  type VisionDebugCamera,
} from "../../api/dashboard";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";
import { navigate } from "../../routes";

const value = (item: unknown) => item === null || item === undefined || item === "" ? "--" : Array.isArray(item) ? item.join(", ") || "없음" : String(item);
// ~/sitting의 LOWER_CONNECTIONS와 같다. 자세 판정과 무관한 상체 관절은 그리지 않는다.
const poseEdges: [number, number][] = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
const lowerJointIndexes = new Set([11, 12, 13, 14, 15, 16]);

function drawOverlay(canvas: HTMLCanvasElement, camera: VisionDebugCamera) {
  const width = camera.frameWidth ?? 0;
  const height = camera.frameHeight ?? 0;
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context || !width || !height) return;
  context.lineWidth = Math.max(2, width / 420);
  context.font = `${Math.max(14, width / 52)}px sans-serif`;
  const box = (item: { x: number; y: number; width: number; height: number; confidence: number | null }, color: string, label: string) => {
    context.strokeStyle = color;
    context.fillStyle = color;
    context.strokeRect(item.x, item.y, item.width, item.height);
    context.fillText(`${label}${item.confidence === null ? "" : ` ${(item.confidence * 100).toFixed(0)}%`}`, item.x + 4, Math.max(17, item.y - 6));
  };
  camera.personBoxes.forEach((item) => box(item, "#31d889", "person"));
  camera.faceBoxes.forEach((item) => box(item, "#b59cff", "face"));
  camera.poseDetections.forEach((pose) => {
    box(pose.box, "#42c7ff", "pose");
    context.strokeStyle = "#42c7ff";
    poseEdges.forEach(([from, to]) => {
      const a = pose.keypoints[from]; const b = pose.keypoints[to];
      if (!a || !b || a.confidence < .12 || b.confidence < .12) return;
      context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
    });
    context.fillStyle = "#fff16d";
    pose.keypoints.forEach((point, index) => {
      if (!lowerJointIndexes.has(index) || point.confidence < (index === 11 || index === 12 ? .08 : .45)) return;
      context.beginPath(); context.arc(point.x, point.y, Math.max(3, width / 190), 0, Math.PI * 2); context.fill();
    });
  });
}

function InferencePreview({ cameraKey, name, camera }: { cameraKey: "upper" | "lower"; name: string; camera?: VisionDebugCamera }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (canvas.current && camera) drawOverlay(canvas.current, camera);
  }, [camera]);
  const version = camera?.observedAt ? encodeURIComponent(camera.observedAt) : "";
  return <article className="video-panel">
    <h2>{name}</h2>
    <div className="debug-preview" style={camera?.frameWidth && camera.frameHeight ? { aspectRatio: `${camera.frameWidth} / ${camera.frameHeight}`, height: "auto" } : undefined}>
      {camera?.frameAvailable ? <><img src={`/api/vision/debug/frame/${cameraKey}?at=${version}`} alt={`${name} 마지막 추론 프레임`} /><canvas ref={canvas} aria-label={`${name} 감지 결과 오버레이`} /></> : <p className="debug-empty">아직 성공한 추론 프레임이 없습니다.</p>}
    </div>
    <p>추론 시각 {camera?.observedAt ? new Date(camera.observedAt).toLocaleTimeString() : "--"} · person {camera?.personBoxes.length ?? 0} · face {camera?.faceBoxes.length ?? 0} · pose {camera?.poseDetections.length ?? 0}{camera?.error ? ` · 오류 ${camera.error}` : ""}</p>
  </article>;
}

export function DebugPanel() {
  const vision = useSnapshotPoll(useCallback((signal) => getVisionStatus(signal), []), 1000);
  // 서버가 0.5초마다 추론한 결과만 읽는다. 이 polling은 detector를 추가 실행하지 않는다.
  const debug = useSnapshotPoll(useCallback((signal) => getVisionDebug(signal), []), 500);
  const user = useSnapshotPoll(useCallback((signal) => getCurrentUser(signal), []), 1000);
  const automation = useSnapshotPoll(useCallback((signal) => getAutomationStatus(signal), []), 1000);
  return <>
    <header className="site-header"><a className="logo" href="/">SMART DESK</a><nav><a href="/debug/voice">AI 스피커 진단</a><button onClick={() => navigate("/")}>대시보드로</button></nav></header>
    <main className="debug-main">
      <section className="debug-heading"><div><p className="eyebrow">VISION DEBUG · 2Hz INFERENCE</p><h1>카메라·상태 전이 진단</h1><p>마지막 추론 프레임을 2Hz로 갱신합니다. person은 재실, face는 신원 후보, 하단 선·점은 pose 관절입니다. 영상은 저장하지 않습니다.</p></div></section>
      <section className="preview-grid">
        <InferencePreview cameraKey="upper" name="상단 카메라 · 재실/얼굴" camera={debug.value?.cameras.upper} />
        <InferencePreview cameraKey="lower" name="하단 카메라 · 자세" camera={debug.value?.cameras.lower} />
      </section>
      <section className="summary-grid">{[["raw 재실", vision.value?.presence.rawStatus], ["stable 재실", vision.value?.presence.status], ["raw 자세", vision.value?.posture.rawStatus], ["stable 자세", vision.value?.posture.status], ["신원", vision.value?.identity.status], ["association", vision.value?.association.usable], ["현재 session", user.value?.session?.sessionId], ["제어 방식", automation.value?.controlMode], ["작업 모드", automation.value?.activityMode?.name], ["자동화", automation.value?.state]].map(([label, item]) => <article key={String(label)}><span>{label}</span><strong>{value(item)}</strong></article>)}</section>
      <section className="detail-grid"><article className="detail-card"><h2>관측·귀속</h2><p>상단/하단 count: {value(vision.value?.presence.upperCount)} / {value(vision.value?.presence.lowerCount)}</p><p>association reasons: {value(vision.value?.association.reasonCodes)}</p><p>identity profile: {value(vision.value?.identity.profileId)}</p></article><article className="detail-card"><h2>자동화 전이</h2><p>차단: {value(automation.value?.blockedReasonCodes)}</p><p>target intent: {value(automation.value?.intentSource)} · target: {value(automation.value?.targetHeightCm)}</p><p>transition: {value(automation.value?.lastTransitionReason)} / {value(automation.value?.lastTransitionSource)}</p></article></section>
      {(vision.error || debug.error || user.error || automation.error) && <p className="connection-error">진단 polling 오류: {[vision.error, debug.error, user.error, automation.error].filter(Boolean).join(" · ")}</p>}
    </main>
  </>;
}
