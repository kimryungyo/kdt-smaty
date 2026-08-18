/** heightGoal 규칙 확인 스크립트.
 *
 * 프론트엔드에 test runner가 없어 번들러로 묶어 node로 돌린다:
 *   node_modules/.bin/rolldown src/features/desk/heightGoal.check.ts \
 *     --format esm --platform node -o /tmp/heightGoal.check.mjs
 *   node /tmp/heightGoal.check.mjs
 */

import assert from "node:assert/strict";

import { chooseGoalHeight, shouldSaveToProfile, type HeightGoalInput } from "./heightGoal";

const base: HeightGoalInput = {
  sittingEdited: false,
  standingEdited: false,
  sittingHeight: 80,
  standingHeight: 110,
  targetHeight: 95,
  posture: "UNKNOWN",
  currentHeight: 95,
};

const cases: [string, number, Partial<HeightGoalInput>][] = [
  ["아무것도 안 고치면 슬라이더 값", 95, {}],
  ["앉기만 고치면 그 값(슬라이더가 따라와 있음)", 80, { sittingEdited: true, targetHeight: 80 }],
  ["서기만 고치면 그 값", 110, { standingEdited: true, targetHeight: 110 }],
  [
    "둘 다 고치고 서 있으면 서기 높이",
    110,
    { sittingEdited: true, standingEdited: true, posture: "STANDING" },
  ],
  [
    "둘 다 고치고 앉아 있으면 앉기 높이",
    80,
    { sittingEdited: true, standingEdited: true, posture: "SITTING" },
  ],
  [
    "자세를 모르면 지금 높이에 가까운 쪽(낮게 있으면 앉기)",
    80,
    { sittingEdited: true, standingEdited: true, posture: "UNKNOWN", currentHeight: 84 },
  ],
  [
    "자세를 모르면 지금 높이에 가까운 쪽(높게 있으면 서기)",
    110,
    { sittingEdited: true, standingEdited: true, posture: null, currentHeight: 108 },
  ],
  [
    "자세도 현재 높이도 없으면 슬라이더를 기준으로 고른다",
    110,
    {
      sittingEdited: true,
      standingEdited: true,
      posture: null,
      currentHeight: null,
      targetHeight: 109,
    },
  ],
];

for (const [label, expected, patch] of cases) {
  const actual = chooseGoalHeight({ ...base, ...patch });
  assert.equal(actual, expected, `${label}: ${actual} !== ${expected}`);
  console.log(`  ✓ ${label} → ${actual}cm`);
}

assert.equal(shouldSaveToProfile("MANUAL"), false, "수동 제어는 저장하지 않는다");
assert.equal(shouldSaveToProfile("AUTO"), true, "자동 제어는 저장한다");
assert.equal(shouldSaveToProfile(null), true, "제어 방식을 모르면 저장한다");
console.log("  ✓ 수동은 저장 안 함 / 자동은 저장");

console.log("\n전부 통과");
