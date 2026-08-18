/** 작업 모드 사용 시간(워크 리듬) 조회 API다. */

export type ModeUsageSlice = { key: string; name: string; seconds: number };
export type ModeUsageDay = { date: string; totalSeconds: number; modes: ModeUsageSlice[] };
export type ModeUsageSummary = {
  from: string;
  to: string;
  totalSeconds: number;
  modes: ModeUsageSlice[];
  days: ModeUsageDay[];
};

/** profileId를 주면 그 사용자 기록만, 없으면 책상 전체 기록을 가져온다. */
export async function getModeUsage(
  days = 7,
  profileId?: string | null,
  signal?: AbortSignal,
): Promise<ModeUsageSummary> {
  const query = new URLSearchParams({ days: String(days) });
  if (profileId) query.set("profileId", profileId);
  const response = await fetch(`/api/activity-modes/usage?${query}`, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("작업 모드 사용 기록을 불러오지 못했습니다.");
  return (await response.json()) as ModeUsageSummary;
}

/** 초를 "2시간 15분"처럼 읽히는 길이로 바꾼다. 0은 빈 문자열이 아니라 0분이다. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (hours === 0) return `${minutes}분`;
  if (minutes === 0) return `${hours}시간`;
  return `${hours}시간 ${minutes}분`;
}

/** 카드처럼 좁은 자리에서 쓰는 짧은 표기다. */
export function formatDurationShort(seconds: number): string {
  const total = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return hours === 0 ? `${minutes}분` : `${hours}.${Math.round((minutes / 60) * 10)}시간`;
}

export const WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"];

export function weekdayOf(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  return WEEKDAY_LABELS[date.getUTCDay()];
}
