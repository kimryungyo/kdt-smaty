/** 높이 패널에서 "설정 적용하기"가 실제로 어디로 갈지 정하는 규칙이다.
 *
 * 분기가 여럿이라 화면에서 떼어 두고 따로 확인한다.
 */

export type Posture = "SITTING" | "STANDING" | string | null;

export type HeightGoalInput = {
  /** 이번에 사용자가 직접 고친 칸 */
  sittingEdited: boolean;
  standingEdited: boolean;
  sittingHeight: number;
  standingHeight: number;
  /** 슬라이더가 가리키는 값. 한 칸만 고쳤으면 그 값이 여기 담겨 있다. */
  targetHeight: number;
  /** 지금 자세. 둘 다 고쳤을 때만 쓴다. */
  posture: Posture;
  /** 자세를 모를 때 기준으로 삼을 현재 책상 높이 */
  currentHeight: number | null;
};

export function chooseGoalHeight(input: HeightGoalInput): number {
  const { sittingEdited, standingEdited, sittingHeight, standingHeight } = input;
  // 한 칸만 고쳤거나 아무것도 안 고쳤으면 슬라이더가 곧 목표다.
  if (!sittingEdited || !standingEdited) return input.targetHeight;

  // 둘 다 고쳤다. 지금 자세에 맞는 쪽으로 간다.
  if (input.posture === "SITTING") return sittingHeight;
  if (input.posture === "STANDING") return standingHeight;

  // 자세를 모르면 지금 높이에 더 가까운 쪽을 이어서 쓴다.
  const here = input.currentHeight ?? input.targetHeight;
  return Math.abs(sittingHeight - here) <= Math.abs(standingHeight - here)
    ? sittingHeight
    : standingHeight;
}

/** 자동 제어일 때만 앉기·서기 높이를 프로필에 남긴다. */
export function shouldSaveToProfile(controlMode: string | null): boolean {
  return controlMode !== "MANUAL";
}
