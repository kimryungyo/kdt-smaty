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
