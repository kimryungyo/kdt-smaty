/** 현재 DeskController와 Dashboard API가 공유하는 사용자 제어 범위다. */
export const DESK_CONTROL_MIN_CM = 75;
export const DESK_CONTROL_MAX_CM = 115;
export const deskControlRangeLabel = `${DESK_CONTROL_MIN_CM}–${DESK_CONTROL_MAX_CM}cm`;

/**
 * 작업 모드에 저장할 수 있는 틸트 단계의 storage 수준 안전 범위다. 실제 장치
 * 한계(TiltSettings.min_level/max_level)를 대체하지 않으며, 실제 장치 범위
 * 검증은 tilt 자동화가 연결된 뒤 API/자동화 계층에서 수행한다.
 */
export const MODE_TILT_LEVEL_MIN = 0;
export const MODE_TILT_LEVEL_MAX = 10;
/** WLED가 받는 밝기 범위. 비워 두면 그 설정은 밝기를 건드리지 않는다. */
export const LED_BRIGHTNESS_MIN = 0;
export const LED_BRIGHTNESS_MAX = 255;
export const MODE_DESCRIPTION_MAX_LENGTH = 300;

/** 논문 값에서 가져온 조명 기본 스케줄. 서버가 새 프로필에 심는 값과 같다. */
export const TIME_OF_DAY_SCHEDULE = {
  kind: "TIME_OF_DAY" as const,
  steps: [
    { at: 7 * 60, color: "FFCB8D", brightness: 102 },   // 3500K · 40%
    { at: 10 * 60, color: "FFE8C3", brightness: 204 },  // 5000K · 80%
    { at: 13 * 60, color: "FFF6D8", brightness: 255 },  // 6000K · 100%
    { at: 18 * 60, color: "FFE0B5", brightness: 140 },  // 4500K · 55%
    { at: 22 * 60, color: "FFBD70", brightness: 77 },   // 3000K · 30%
  ],
};

export const ELAPSED_SCHEDULE = {
  kind: "ELAPSED" as const,
  steps: [
    { at: 0, color: "FFD6A4", brightness: 153 },        // 4000K · 60%
    { at: 4, color: "FFE0B5", brightness: 179 },        // 4500K · 70%
    { at: 8, color: "FFE8C3", brightness: 217 },        // 5000K · 85%
    { at: 10, color: "FFF6D8", brightness: 255 },       // 6000K · 100%
  ],
};
