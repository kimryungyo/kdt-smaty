# Relay pinmap

| ESP32-C3 | 기능 | 극성 | 안전 기본값 |
| ---: | --- | --- | --- |
| GPIO 3 | UP relay | active-high | LOW/OFF |
| GPIO 4 | DOWN relay | active-high | LOW/OFF |

릴레이 접점은 책상 조작 패널의 저전압 버튼 접점에만 연결한다. 모터 전원이나 AC
주전원을 직접 개폐하지 않는다. 핀과 극성은 runtime 설정으로 바꾸지 않는다.
