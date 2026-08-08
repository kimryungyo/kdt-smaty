type Props = { css: string };

/** 원본 정적 페이지의 스타일을 현재 렌더링 중인 화면에 그대로 적용한다. */
export function LegacyStyle({ css }: Props) {
  return <style>{css}</style>;
}
