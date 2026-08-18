import { useCallback, useEffect, useMemo, useState } from "react";

import {
  formatDuration,
  getModeUsage,
  weekdayOf,
  type ModeUsageSummary,
} from "../../api/modeUsage";
import { getCurrentUser, listProfiles, type Profile } from "../../api/dashboard";
import { useSnapshotPoll } from "../../hooks/useSnapshotPoll";
import { navigate } from "../../routes";
import { OTHER_KEY, OTHER_NAME, SERIES_SLOTS, assignSlots, colorFor } from "./palette";
import "./work-rhythm.css";

type Series = { key: string; name: string; seconds: number; color: string };

/** 6번째부터는 새 색을 만들지 않고 '기타'로 접는다. */
function foldSeries(summary: ModeUsageSummary) {
  const ranked = summary.modes.map((mode) => mode.key);
  const slots = assignSlots(ranked);
  const kept = summary.modes.slice(0, SERIES_SLOTS);
  const folded = summary.modes.slice(SERIES_SLOTS);
  const series: Series[] = kept.map((mode) => ({
    key: mode.key, name: mode.name, seconds: mode.seconds, color: colorFor(mode.key, slots),
  }));
  if (folded.length > 0) {
    series.push({
      key: OTHER_KEY, name: OTHER_NAME,
      seconds: folded.reduce((sum, mode) => sum + mode.seconds, 0),
      color: colorFor(OTHER_KEY, slots),
    });
  }
  const secondsFor = (day: ModeUsageSummary["days"][number], key: string) =>
    key === OTHER_KEY
      ? day.modes.filter((mode) => !slots.has(mode.key)).reduce((sum, mode) => sum + mode.seconds, 0)
      : day.modes.find((mode) => mode.key === key)?.seconds ?? 0;
  return { series, secondsFor };
}

export function WorkRhythm() {
  // 기록은 사람마다 따로 쌓인다. 기본은 지금 인식된 본인이고, 필요하면 다른
  // 사용자를 골라 볼 수 있다.
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    void Promise.all([listProfiles(), getCurrentUser()])
      .then(([all, current]) => {
        if (!alive) return;
        setProfiles(all);
        const recognized = current.session?.kind === "REGISTERED" ? current.session.profileId : null;
        setSelected(recognized ?? all[0]?.id ?? null);
      })
      .catch(() => { if (alive) setProfiles([]); })
      .finally(() => { if (alive) setReady(true); });
    return () => { alive = false; };
  }, []);

  const usage = useSnapshotPoll(
    useCallback((signal: AbortSignal) => getModeUsage(7, selected, signal), [selected]),
    30000,
    ready && selected !== null,
  );
  const summary = usage.value;
  const view = useMemo(() => (summary ? foldSeries(summary) : null), [summary]);

  const owner = profiles.find((profile) => profile.id === selected) ?? null;

  if (ready && selected === null) {
    return <div className="rhythm-page rhythm-root"><main className="rhythm-main">
      <p className="rhythm-empty">아직 등록된 사용자가 없습니다. 프로필을 만들면 기록이 쌓입니다.</p>
    </main></div>;
  }
  if (!summary || !view) {
    return <div className="rhythm-page rhythm-root"><main className="rhythm-main">
      <p className="rhythm-empty">{usage.error ?? "워크 리듬을 불러오는 중입니다."}</p>
    </main></div>;
  }

  const { series, secondsFor } = view;
  const peak = Math.max(1, ...summary.days.map((day) => day.totalSeconds));
  const activeDays = summary.days.filter((day) => day.totalSeconds > 0).length;
  const busiest = summary.days.reduce(
    (best, day) => (day.totalSeconds > best.totalSeconds ? day : best), summary.days[0],
  );
  const top = series[0];

  return <div className="rhythm-page rhythm-root">
    <main className="rhythm-main">
      <header className="rhythm-head">
        <div>
          <p>WORK RHYTHM</p>
          <h1>워크 리듬</h1>
          <span>{owner ? `${owner.name}님이 ` : ""}최근 7일 동안 작업 모드를 얼마나 오래 썼는지 보여줍니다.</span>
        </div>
        <div className="rhythm-head-actions">
          {profiles.length > 1 && <label className="rhythm-picker">사용자
            <select value={selected ?? ""} onChange={(event) => setSelected(event.target.value)}>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </label>}
          <button type="button" className="rhythm-back" onClick={() => navigate("/")}>대시보드로</button>
        </div>
      </header>

      <section className="rhythm-hero">
        <article className="rhythm-stat"><span>7일 합계</span><b>{formatDuration(summary.totalSeconds)}</b>
          <small>기록된 날 {activeDays}일</small></article>
        <article className="rhythm-stat"><span>가장 많이 쓴 모드</span>
          <b>{top ? top.name : "기록 없음"}</b>
          {top && <small><i className="rhythm-swatch" style={{ background: top.color }} />{formatDuration(top.seconds)}</small>}</article>
        <article className="rhythm-stat"><span>가장 오래 앉은 날</span>
          <b>{busiest.totalSeconds > 0 ? `${busiest.date.slice(5)} (${weekdayOf(busiest.date)})` : "기록 없음"}</b>
          <small>{formatDuration(busiest.totalSeconds)}</small></article>
      </section>

      <section className="rhythm-card">
        <h2>날짜별 사용 시간</h2>
        <p>하루를 모드별로 쌓아 보여줍니다. 막대에 마우스를 올리면 그날의 내역이 나옵니다.</p>
        {series.length === 0 ? <p className="rhythm-empty">아직 기록된 작업 모드 사용이 없습니다.</p> : <>
          <div className="rhythm-legend">
            {series.map((item) => <span key={item.key}>
              <i className="rhythm-swatch" style={{ background: item.color }} />{item.name}
            </span>)}
          </div>
          <div className="rhythm-bars">
            {summary.days.map((day) => {
              const detail = series
                .map((item) => ({ item, seconds: secondsFor(day, item.key) }))
                .filter((entry) => entry.seconds > 0);
              const title = detail.length === 0
                ? `${day.date} · 기록 없음`
                : `${day.date}\n${detail.map((entry) => `${entry.item.name} ${formatDuration(entry.seconds)}`).join("\n")}`;
              return <div className="rhythm-day" key={day.date} title={title}>
                {day.totalSeconds === 0
                  ? <div className="rhythm-empty-day" />
                  : <div className="rhythm-stack" style={{ height: `${(day.totalSeconds / peak) * 100}%` }}>
                      {detail.map((entry) => <div
                        key={entry.item.key}
                        className="rhythm-seg"
                        style={{
                          background: entry.item.color,
                          flexGrow: entry.seconds,
                          flexBasis: 0,
                        }}
                      />)}
                    </div>}
                <span className="rhythm-daylabel"><b>{weekdayOf(day.date)}</b>{day.date.slice(5)}</span>
              </div>;
            })}
          </div>
        </>}
      </section>

      {series.length > 0 && <section className="rhythm-card">
        <h2>모드별 비중</h2>
        <p>7일 합계에서 각 모드가 차지한 시간입니다.</p>
        <div className="rhythm-split">
          {series.map((item) => <div className="rhythm-row" key={item.key}>
            <span><i className="rhythm-swatch" style={{ background: item.color }} /><i>{item.name}</i></span>
            <div className="rhythm-track">
              <div className="rhythm-fill" style={{
                width: `${summary.totalSeconds > 0 ? (item.seconds / summary.totalSeconds) * 100 : 0}%`,
                background: item.color,
              }} />
            </div>
            <b>{formatDuration(item.seconds)}</b>
          </div>)}
        </div>
      </section>}

      {series.length > 0 && <section className="rhythm-card">
        <h2>표로 보기</h2>
        <p>같은 값을 숫자로 확인할 수 있습니다.</p>
        <div className="rhythm-scroll">
          <table className="rhythm-table">
            <thead><tr><th>날짜</th>{series.map((item) => <th key={item.key}>{item.name}</th>)}<th>합계</th></tr></thead>
            <tbody>
              {summary.days.map((day) => <tr key={day.date}>
                <td>{day.date.slice(5)} ({weekdayOf(day.date)})</td>
                {series.map((item) => <td key={item.key}>{formatDuration(secondsFor(day, item.key))}</td>)}
                <td>{formatDuration(day.totalSeconds)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
        <p className="rhythm-note">자리를 비운 동안에는 시간이 늘지 않습니다. 자리를 뜬 뒤 30분 안에 돌아오면 쓰던 모드로 이어서 기록합니다.</p>
      </section>}
    </main>
  </div>;
}
