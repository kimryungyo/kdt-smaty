/** 작업 모드에 색을 고정 배정한다.
 *
 * 색은 순위가 아니라 모드(entity)를 따라간다. 그래서 목록이 바뀌어도 남은
 * 모드의 색이 다시 칠해지지 않도록, 모드 key를 순서대로 slot에 못박는다.
 * 검증된 categorical 순서를 그대로 쓰고 9번째부터는 새 색을 만들지 않고
 * '기타'로 접는다.
 */

export const SERIES_SLOTS = 5;
export const OTHER_KEY = "__other__";
export const OTHER_NAME = "기타";

/** 모드 key → slot 번호(1부터). 등장 순서가 아니라 총 사용량 순서로 고정한다. */
export function assignSlots(keysByUsage: string[]): Map<string, number> {
  const slots = new Map<string, number>();
  keysByUsage.slice(0, SERIES_SLOTS).forEach((key, index) => slots.set(key, index + 1));
  return slots;
}

export function slotVar(slot: number): string {
  return `var(--series-${slot})`;
}

export function colorFor(key: string, slots: Map<string, number>): string {
  const slot = slots.get(key);
  return slot === undefined ? "var(--series-other)" : slotVar(slot);
}
