#!/bin/sh
# 틸트 BTS7960은 20kHz PWM으로 구동한다. lgpio 소프트웨어 PWM은 10kHz가
# 상한이라 하드웨어 PWM(sysfs)을 쓰는데, 그 노드는 root 소유라 컨테이너의
# 서비스 uid가 열 수 없다. 부팅 시 채널을 미리 export하고 gpio 그룹에 넘긴다.
set -e

for chip in /sys/class/pwm/pwmchip*; do
    [ -e "$chip" ] || continue
    chgrp gpio "$chip/export" "$chip/unexport" 2>/dev/null || true
    chmod g+w "$chip/export" "$chip/unexport" 2>/dev/null || true

    for channel in 0 1; do
        [ -e "$chip/pwm$channel" ] || echo "$channel" > "$chip/export" 2>/dev/null || true
    done

    for channel in 0 1; do
        node="$chip/pwm$channel"
        [ -e "$node" ] || continue
        chgrp -R gpio "$node" 2>/dev/null || true
        chmod -R g+w "$node" 2>/dev/null || true
    done
done
