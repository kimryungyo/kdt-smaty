import { useCallback, useEffect, useState } from "react";

import { getAssistantLatest, type AssistantTurn } from "../../api/dashboard";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";
import { VoiceStatusPanel } from "../voice/VoiceStatusPanel";

type Props = { currentSessionId: string | null; currentSessionKind: "REGISTERED" | "ANONYMOUS" | null; registeredProfileName: string | null };

const phaseText = (turn: AssistantTurn) => {
  if (turn.status === "SUCCEEDED") return "최종 응답을 완료했습니다.";
  if (turn.status === "CANCELLED") return "요청이 취소되었습니다.";
  if (turn.status === "FAILED") return "요청을 처리하지 못했습니다.";
  if (turn.phase === "LISTENING") return "음성 요청을 듣고 있습니다.";
  if (turn.phase === "PROCESSING") return "요청을 처리하고 있습니다.";
  if (turn.phase === "TOOL") return "필요한 기능을 실행하고 있습니다.";
  return "최종 응답을 준비하고 있습니다.";
};

const freshness = (lastSuccessAt: number | null) => lastSuccessAt === null ? "아직 성공 snapshot 없음" : `마지막 성공 ${Math.max(0, Math.round((Date.now() - lastSuccessAt) / 1000))}초 전`;

export function AssistantPanel({ currentSessionId, currentSessionKind, registeredProfileName }: Props) {
  const latest = useSnapshotPoll(useCallback((signal) => getAssistantLatest(signal), []), 1000);
  const [acceptedTurn, setAcceptedTurn] = useState<AssistantTurn | null>(null);

  useEffect(() => {
    const incoming = latest.value?.turn ?? null;
    setAcceptedTurn((current) => {
      if (incoming === null || incoming.sessionId !== currentSessionId) return null;
      if (current === null || current.turnId !== incoming.turnId) return incoming;
      if (current.status !== "RUNNING" || incoming.sequence <= current.sequence) return current;
      return incoming;
    });
  }, [currentSessionId, latest.value]);

  const turn = acceptedTurn?.sessionId === currentSessionId ? acceptedTurn : null;
  const personalized = turn !== null && currentSessionKind === "REGISTERED";
  const owner = personalized ? `${registeredProfileName ?? "등록 사용자"}의 현재 session 응답` : "비개인화 게스트 응답 · 장기 기억을 사용하지 않습니다.";

  return <><VoiceStatusPanel /><section className="card dashboard-section assistant-panel" aria-label="Assistant 응답">
    <p className="card-label">ASSISTANT · 최신 응답</p>
    {turn ? <>
      <h2>{turn.title}</h2>
      <p className="assistant-state" role="status" aria-live="polite">{phaseText(turn)}</p>
      <p className="control-note">{owner}</p>
      {turn.summary && <p className="assistant-summary">{turn.summary}</p>}
      {turn.detail && <details className="assistant-detail"><summary>상세 보기</summary><p>{turn.detail}</p></details>}
      {turn.status === "FAILED" && turn.errorCode && <p className="inline-error" role="status">오류 코드: {turn.errorCode}</p>}
    </> : <>
      <h2>표시할 Assistant 응답 없음</h2>
      <p className="control-note">현재 session과 일치하는 최신 음성 요청·응답만 표시합니다.</p>
    </>}
    <p className={latest.error ? "assistant-freshness is-stale" : "assistant-freshness"} role="status">
      {latest.error ? `polling 오류 · ${latest.error}. 마지막으로 안전하게 확인한 응답을 표시합니다.` : freshness(latest.lastSuccessAt)}
    </p>
  </section></>;
}
