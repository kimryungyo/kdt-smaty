"""환경과 관계없이 유지되는 애플리케이션 상수."""

APP_NAME = "SMART DESK"
SINGLE_PROCESS_WORKERS = 1

# 모션데스크 자체의 고정 물리 높이 범위다.
DESK_PHYSICAL_MIN_CM = 73.0
DESK_PHYSICAL_MAX_CM = 118.0

# 자동 목표와 수동 이동에 허용하는 고정 제어 범위다.
DESK_CONTROL_MIN_CM = 75.0
DESK_CONTROL_MAX_CM = 115.0
