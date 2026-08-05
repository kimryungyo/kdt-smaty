"""기존 SMART DESK 서비스와 ESP32가 사용하는 MQTT 토픽."""

VISION_TOPIC = "/smartdesk/vision"
VISION_STATUS_TOPIC = "/smartdesk/vision/status"
VISION_COMMAND_TOPIC = "/smartdesk/vision/command"

HEIGHT_TOPIC = "/smartdesk/desk/height"
DESK_COMMAND_TOPIC = "/smartdesk/desk/command"
DESK_STATUS_TOPIC = "/smartdesk/desk/status"

DASHBOARD_STATUS_TOPIC = "/smartdesk/dashboard/status"
WLED_COMMAND_TOPIC = "/smartdesk/wled/command"

# 기존 ESP32 펌웨어와의 호환을 위해 이 두 토픽은 변경하지 않는다.
ESP32_COMMAND_TOPIC = "/desk_ctl"
ESP32_STATUS_TOPIC = "/desk_ctl_status"
