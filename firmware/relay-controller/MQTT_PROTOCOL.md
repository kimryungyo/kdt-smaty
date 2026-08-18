# FIN relay MQTT protocol

| 흐름 | topic | QoS | retain |
| --- | --- | ---: | --- |
| Python → ESP32 | `/desk_ctl` | 1 | false |
| height monitor → ESP32 | `/smartdesk/desk/height` | 1 | true |
| ESP32 → Python | `/desk_ctl_status` | 0 | false |

이동 명령은 정확히 `command`, `source`, `hold_ms` 세 필드만 허용한다.

```json
{"command":"UP","source":"desk_service","hold_ms":500}
```

절전 표시기를 깨울 때만 `WAKE`를 사용한다. 마지막 실제 높이(`basis_height_cm`)의 물리·방향
경계 안에서 정확히 400ms 한 번만 relay pulse를 허용하며, 이 명령은 height lease를 무장하지
않는다. 이후 서로 다른 fresh height 관측과 arming delay를 다시 통과해야 일반 UP/DOWN이 가능하다.

```json
{"command":"WAKE","source":"desk_service","direction":"DOWN","hold_ms":400,"basis_height_cm":110.0}
```

STOP은 정확히 다음 한 필드 메시지만 허용하며 이미 정지 상태여도 live `stopped`
응답을 발행한다.

```json
{"command":"STOP"}
```

높이는 `schema`, `observed_at`, `height_cm` 세 필드만 허용한다. MQTT session마다 첫
valid 높이는 baseline으로만 저장하며, 다른 `observed_at`의 높이와 arming delay가
확인된 뒤에만 UP/DOWN을 허용한다. 같은 `observed_at` duplicate는 lease를 갱신하지
않는다.
