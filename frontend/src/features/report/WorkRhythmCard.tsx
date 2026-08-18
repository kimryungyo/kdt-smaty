import { useCallback, useMemo } from "react";

import {
  formatDuration,
  getModeUsage,
  weekdayOf,
} from "../../api/modeUsage";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";
import { navigate } from "../../routes";
import { SERIES_SLOTS, assignSlots, colorFor } from "./palette";
import "./work-rhythm.css";

/** 대시보드용 요약 카드. 인식된 사용자 본인의 기록만 단순하게 보여준다. */
export function WorkRhythmCard({ className, profileId, profileName }: {
  className?: string;
  profileId: string | null;
  profileName: string | null;
}) {
  const usage = useSnapshotPoll(
    useCallback((signal: AbortSignal) => getModeUsage(7, profileId, signal), [profileId]),
    60000,
    Boolean(profileId),
  );
  const summary = usage.value;

  const view = useMemo(() => {
    if (!summary) return null;
    const slots = assignSlots(summary.modes.slice(0, SERIES_SLOTS).map((mode) => mode.key));
    const peak = Math.max(1, ...summary.days.map((day) => day.totalSeconds));
    return { slots, peak, top: summary.modes[0] ?? null };
  }, [summary]);

  return <button
    type="button"
    className={`card rhythm-root ${className ?? ""}`}
    onClick={() => navigate("/reports/work-rhythm")}
  >
    <div className="cardtop"><span className="ico" aria-hidden="true">◴</span><span>↗</span></div>
    <small>워크 리듬{profileName ? ` · ${profileName}` : ""}</small>
    <h2>{!profileId ? "인식 대기" : summary ? formatDuration(summary.totalSeconds) : "--"}</h2>
    {summary && view && <div className="rhythm-mini" aria-hidden="true">
      {summary.days.map((day) => <i
        key={day.date}
        title={`${weekdayOf(day.date)} ${formatDuration(day.totalSeconds)}`}
        style={{
          height: `${Math.max(4, (day.totalSeconds / view.peak) * 100)}%`,
          background: day.totalSeconds > 0
            ? colorFor(day.modes[0]?.key ?? "", view.slots)
            : "var(--grid)",
        }}
      />)}
    </div>}
    <p>{!profileId ? "얼굴이 인식되면 내 기록이 쌓여요"
      : summary && view?.top ? `${view.top.name} ${formatDuration(view.top.seconds)}`
      : "최근 7일 기록 없음"}<b>›</b></p>
  </button>;
}
