import { useCallback, useEffect, useRef, useState } from "react";

import {
  type DeskStatus,
  type Direction,
  ApiError,
  cancelTarget,
  sendHold,
  sendStop,
  setTarget,
} from "../../api/dashboard";
import {
  DESK_CONTROL_MAX_CM,
  DESK_CONTROL_MIN_CM,
} from "../../config";

type Props = {
  status: DeskStatus | null;
  canControl: boolean;
  controlError: string | null;
  onStatus: (status: DeskStatus) => void;
  onError: (message: string) => void;
};

export function DeskPanel({ status, canControl, controlError, onStatus, onError }: Props) {
  const [target, setTargetValue] = useState("");
  const direction = useRef<Direction | null>(null);
  const holdInFlight = useRef(false);
  const interval = useRef<number | null>(null);

  const stopHolding = useCallback(async (force = false) => {
    if (interval.current !== null) {
      window.clearInterval(interval.current);
      interval.current = null;
    }
    if (direction.current === null && !force) return;
    direction.current = null;
    try {
      onStatus(await sendStop());
    } catch (error) {
      onError(error instanceof Error ? error.message : "정지 요청을 보내지 못했습니다.");
    }
  }, [onError, onStatus]);

  const beginHolding = useCallback(
    async (nextDirection: Direction) => {
      if (!canControl || direction.current === nextDirection) return;
      if (direction.current !== null) await stopHolding();

      direction.current = nextDirection;
      const refresh = async () => {
        if (holdInFlight.current || direction.current !== nextDirection) return;
        holdInFlight.current = true;
        try {
          onStatus(await sendHold(nextDirection));
        } catch (error) {
          onError(error instanceof Error ? error.message : "수동 이동 요청을 보내지 못했습니다.");
          if (error instanceof ApiError && error.status === 409) {
            // 범위·STOP 진행 중 같은 명시적 거부는 relay를 건드리지 않는다.
            direction.current = null;
            if (interval.current !== null) {
              window.clearInterval(interval.current);
              interval.current = null;
            }
          } else {
            void stopHolding();
          }
        } finally {
          holdInFlight.current = false;
        }
      };
      void refresh();
      interval.current = window.setInterval(() => void refresh(), 200);
    },
    [canControl, onError, onStatus, stopHolding],
  );

  useEffect(() => {
    const stop = () => void stopHolding();
    const hidden = () => {
      if (document.visibilityState === "hidden") stop();
    };
    const pageHide = () => {
      if (interval.current !== null) {
        window.clearInterval(interval.current);
        interval.current = null;
      }
      if (direction.current !== null) {
        direction.current = null;
        void sendStop(true).catch(() => undefined);
      }
    };
    window.addEventListener("blur", stop);
    window.addEventListener("pagehide", pageHide);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("blur", stop);
      window.removeEventListener("pagehide", pageHide);
      document.removeEventListener("visibilitychange", hidden);
      void stopHolding();
    };
  }, [stopHolding]);

  const submitTarget = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = Number(target);
    if (!Number.isFinite(value)) {
      onError("목표 높이를 숫자로 입력해 주세요.");
      return;
    }
    try {
      onStatus(await setTarget(value));
    } catch (error) {
      onError(error instanceof Error ? error.message : "목표 높이를 설정하지 못했습니다.");
    }
  };

  const cancel = async () => {
    try {
      onStatus(await cancelTarget());
    } catch (error) {
      onError(error instanceof Error ? error.message : "목표 이동을 취소하지 못했습니다.");
    }
  };

  const press = (nextDirection: Direction, event: React.PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    void beginHolding(nextDirection);
  };
  const keyDown = (nextDirection: Direction, event: React.KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      void beginHolding(nextDirection);
    }
  };

  const heightStatus = status?.height.status;
  const statusLabel = status?.state === "WAKING"
    ? "센서 깨우는 중"
    : heightStatus === "SENSOR_SLEEPING" || heightStatus === "STALE"
      ? "센서 절전 상태"
      : canControl ? "연결됨" : "연결 확인 중";

  return (
    <section className="control-grid" aria-label="데스크 직접 제어">
      <article className="card control-card">
        <div className="card-header"><div><p className="card-label">MANUAL CONTROL</p><h2>모터 수동 제어</h2></div><span className={`live-status ${canControl ? "" : "offline"}`}><span /><b>{statusLabel}</b></span></div>
        <div className="height-readout"><span>현재 높이</span><strong>{status?.height.heightCm?.toFixed(1) ?? "--.-"}<small>cm</small></strong></div>
        {(controlError || status?.lastError || status?.relay.lastError) && <p className="inline-error">{controlError ?? status?.lastError ?? status?.relay.lastError}</p>}
        <div className="hold-buttons" aria-label="수동 높이 조절">
          {(["UP", "DOWN"] as const).map((value) => <button className="hold-button" type="button" key={value} disabled={!canControl} aria-label={value === "UP" ? "누르는 동안 책상 올리기" : "누르는 동안 책상 내리기"} onPointerDown={(event) => press(value, event)} onPointerUp={() => void stopHolding()} onPointerCancel={() => void stopHolding()} onLostPointerCapture={() => void stopHolding()} onKeyDown={(event) => keyDown(value, event)} onKeyUp={(event) => { if (event.key === " " || event.key === "Enter") void stopHolding(); }}>
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d={value === "UP" ? "M12 19V5m0 0-6 6m6-6 6 6" : "M12 5v14m0 0 6-6m-6 6-6-6"} /></svg>{value === "UP" ? "올리기" : "내리기"}
          </button>)}
        </div>
        <button className="stop-button" type="button" onClick={() => void stopHolding(true)}>정지</button>
        <p className="control-note">{status?.state === "WAKING" ? "높이 센서를 한 번 깨운 뒤 새 관측과 릴레이 준비 상태를 확인하고 있습니다." : "버튼을 누르고 있는 동안만 이동하며, 손을 떼거나 화면을 벗어나거나 연결이 끊기면 즉시 정지합니다."}</p>
      </article>
      <article className="card control-card">
        <div className="card-header"><div><p className="card-label">AUTO MOVE</p><h2>목표 높이로 자동 이동</h2></div><span className="card-number">06</span></div>
        <form onSubmit={(event) => void submitTarget(event)}>
          <label className="height-field" htmlFor="targetHeightInput"><span>목표 높이 입력</span><div><input id="targetHeightInput" type="number" min={DESK_CONTROL_MIN_CM} max={DESK_CONTROL_MAX_CM} step="0.1" value={target} onChange={(event) => setTargetValue(event.target.value)} disabled={!canControl} required /><span>cm</span></div></label>
          <div className="target-actions"><button type="button" className="previous-button" onClick={() => void cancel()} disabled={status?.targetHeightCm === null || status?.targetHeightCm === undefined}>취소</button><button className="complete-button" type="submit" disabled={!canControl}>이동 시작</button></div>
        </form>
        <p className="status-message" role="status">직접 목표는 session 없이도 사용할 수 있으며, API 접수 뒤 실제 이동 상태를 확인합니다.</p>
      </article>
    </section>
  );
}
