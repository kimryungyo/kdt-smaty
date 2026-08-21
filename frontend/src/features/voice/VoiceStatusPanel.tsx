import { useCallback } from "react";

import { getVoiceStatus, type VoiceStatus } from "../../api/dashboard";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";

const stateText: Record<VoiceStatus["state"], string> = {
  DISABLED: "음성 기능이 비활성화되어 있습니다.",
  WAITING_WAKE: "호출어를 기다리고 있습니다.",
  WAITING_FOLLOWUP: "추가 발화를 기다리고 있습니다.",
  ACKNOWLEDGING: "호출어를 확인하고 녹음을 준비하고 있습니다.",
  RECORDING: "음성을 듣고 있습니다.",
  PROCESSING: "음성 요청을 처리하고 있습니다.",
  SPEAKING: "음성으로 응답하고 있습니다.",
  ERROR: "음성 기능에 오류가 있습니다.",
};

const freshness = (lastSuccessAt: number | null) => lastSuccessAt === null
  ? "아직 성공 snapshot 없음"
  : `마지막 성공 ${Math.max(0, Math.round((Date.now() - lastSuccessAt) / 1000))}초 전`;

export function VoiceStatusPanel() {
  const voice = useSnapshotPoll(useCallback((signal) => getVoiceStatus(signal), []), 1000);
  const snapshot = voice.value;

  return <section className="card dashboard-section voice-status-panel" aria-label="음성 상태">
    <p className="card-label">VOICE STATUS · 음성 상태</p>
    <h2>{snapshot?.state ?? "확인 중"}</h2>
    <p className="control-note">{snapshot ? stateText[snapshot.state] : "음성 상태를 확인하고 있습니다."}</p>
    {snapshot?.state === "WAITING_FOLLOWUP" && snapshot.followupExpiresAt && <p className="control-note">추가 발화 대기 시간이 설정되어 있습니다.</p>}
    {snapshot?.state === "ERROR" && snapshot.lastError && <p className="inline-error" role="status">오류 코드: {snapshot.lastError}</p>}
    <p className={voice.error ? "assistant-freshness is-stale" : "assistant-freshness"} role="status">
      {voice.error ? `polling 오류 · ${voice.error}. 마지막으로 안전하게 확인한 상태를 표시합니다.` : freshness(voice.lastSuccessAt)}
    </p>
  </section>;
}
