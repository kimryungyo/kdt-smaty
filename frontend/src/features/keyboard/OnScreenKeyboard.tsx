import { useCallback, useEffect, useRef, useState } from "react";

// 터치 디스플레이(kiosk)에는 물리 키보드가 없고, Wayland 온스크린 키보드는
// Chromium 창 위에서 뜨지 않는다. 그래서 대시보드가 직접 키보드를 그린다.
// 페이지 안의 DOM이므로 브라우저·컴포지터 설정과 무관하게 언제나 동작한다.

const DIGIT_ROW = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];

const LETTER_ROWS = [
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["z", "x", "c", "v", "b", "n", "m"],
];

// React가 관리하는 input에 값을 넣으려면 네이티브 setter를 거쳐야 한다.
// value를 직접 대입하면 React의 내부 추적값과 어긋나 onChange가 돌지 않는다.
const setNativeValue = (element: HTMLInputElement | HTMLTextAreaElement, value: string) => {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
};

// 키보드가 필요한 입력만 고른다. 색상·범위·체크박스 등은 터치로 직접 조작한다.
const TEXTUAL_TYPES = new Set(["text", "search", "tel", "url", "email", "password", "number"]);

const isTextual = (node: Element | null): node is HTMLInputElement | HTMLTextAreaElement => {
  if (node instanceof HTMLTextAreaElement) {
    return !node.disabled && !node.readOnly;
  }
  if (!(node instanceof HTMLInputElement)) {
    return false;
  }
  return TEXTUAL_TYPES.has(node.type) && !node.disabled && !node.readOnly;
};

export const OnScreenKeyboard = () => {
  const [target, setTarget] = useState<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const [shift, setShift] = useState(false);
  // 키를 누르는 순간 input에서 focus가 빠져나가면 키보드가 닫힌다.
  // pointerdown에서 기본 동작을 막아 focus를 유지하되, blur는 한 박자 늦게 확인한다.
  const closeTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    const onFocusIn = (event: FocusEvent) => {
      window.clearTimeout(closeTimer.current);
      const node = event.target as Element | null;
      if (isTextual(node)) {
        setTarget(node);
      }
    };
    const onFocusOut = () => {
      closeTimer.current = window.setTimeout(() => {
        if (!isTextual(document.activeElement)) {
          setTarget(null);
          setShift(false);
        }
      }, 0);
    };
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      window.clearTimeout(closeTimer.current);
    };
  }, []);

  // 키보드가 화면 아래를 덮으므로, 가려진 입력창을 보이는 자리로 끌어올린다.
  useEffect(() => {
    if (target) {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [target]);

  const press = useCallback((key: string) => {
    if (!target) {
      return;
    }
    const start = target.selectionStart ?? target.value.length;
    const end = target.selectionEnd ?? target.value.length;
    // number 입력은 selection API를 지원하지 않아 start/end가 null로 온다.
    // 그때는 문자열 끝에 이어 붙인다.
    const next = target.value.slice(0, start) + key + target.value.slice(end);
    setNativeValue(target, next);
    const caret = start + key.length;
    try {
      target.setSelectionRange(caret, caret);
    } catch {
      // number 등 selection을 못 쓰는 입력은 캐럿 이동을 건너뛴다.
    }
    if (shift) {
      setShift(false);
    }
  }, [shift, target]);

  const backspace = useCallback(() => {
    if (!target) {
      return;
    }
    const start = target.selectionStart ?? target.value.length;
    const end = target.selectionEnd ?? target.value.length;
    // 선택 영역이 있으면 그 부분을, 없으면 캐럿 앞 한 글자를 지운다.
    const from = start === end ? Math.max(0, start - 1) : start;
    const next = target.value.slice(0, from) + target.value.slice(end);
    setNativeValue(target, next);
    try {
      target.setSelectionRange(from, from);
    } catch {
      // 위와 같다.
    }
  }, [target]);

  const done = useCallback(() => {
    target?.blur();
    setTarget(null);
    setShift(false);
  }, [target]);

  if (!target) {
    return null;
  }

  // pointerdown을 막아야 입력창의 focus가 유지된다. click으로 실제 동작을 건다.
  const hold = (event: React.PointerEvent) => event.preventDefault();
  const label = (key: string) => (shift ? key.toUpperCase() : key);

  return (
    <div className="osk" onPointerDown={hold}>
      <div className="osk-row">
        {DIGIT_ROW.map((key) => (
          <button type="button" className="osk-key" key={key} onClick={() => press(key)}>
            {key}
          </button>
        ))}
        <button type="button" className="osk-key osk-back" onClick={backspace} aria-label="한 글자 지우기">
          ⌫
        </button>
      </div>
      <div className="osk-row">
        {LETTER_ROWS[0].map((key) => (
          <button type="button" className="osk-key" key={key} onClick={() => press(label(key))}>
            {label(key)}
          </button>
        ))}
      </div>
      <div className="osk-row">
        <span className="osk-pad" aria-hidden="true" />
        {LETTER_ROWS[1].map((key) => (
          <button type="button" className="osk-key" key={key} onClick={() => press(label(key))}>
            {label(key)}
          </button>
        ))}
        <span className="osk-pad" aria-hidden="true" />
      </div>
      <div className="osk-row">
        <button type="button" className="osk-key osk-shift" onClick={() => setShift((on) => !on)} aria-pressed={shift}>
          {shift ? "⬆" : "⇧"}
        </button>
        {LETTER_ROWS[2].map((key) => (
          <button type="button" className="osk-key" key={key} onClick={() => press(label(key))}>
            {label(key)}
          </button>
        ))}
        <button type="button" className="osk-key osk-done" onClick={done}>
          완료
        </button>
      </div>
      <div className="osk-row">
        <button type="button" className="osk-key osk-space" onClick={() => press(" ")} aria-label="띄어쓰기" />
      </div>
    </div>
  );
};
