import { useEffect, useState } from "react";

import { type Profile, createProfile, deleteProfile, listProfiles, updateProfile } from "../../api/dashboard";
import { DESK_CONTROL_MAX_CM, DESK_CONTROL_MIN_CM } from "../../config";
import { LegacyStyle } from "../../legacy/LegacyStyle";
import pickerCss from "../../legacy/profiles.css?raw";
import profileFormCss from "../../legacy/profile-form.css?raw";
import deskSetupCss from "../../legacy/desk-setup.css?raw";

type PickerProps = { onSelect: (profile: Profile) => void; onCreate: () => void };

export function ProfilePicker({ onSelect, onCreate }: PickerProps) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const refresh = async () => {
    setLoading(true);
    try { setProfiles(await listProfiles()); setMessage(""); }
    catch (error) { setMessage(error instanceof Error ? error.message : "프로필을 불러오지 못했습니다."); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  const remove = async (profile: Profile, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!window.confirm(`'${profile.name}' 프로필을 삭제할까요? 저장된 높이 설정도 함께 삭제됩니다.`)) return;
    try { await deleteProfile(profile.id); await refresh(); setMessage(`'${profile.name}' 프로필을 삭제했습니다.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "프로필을 삭제하지 못했습니다."); }
  };
  return <><LegacyStyle css={pickerCss} /><main><section className="page-heading"><p className="step-label">SELECT PROFILE</p><h1>누구의 책상인가요?</h1><p>프로필을 선택하면 저장된 높이로 책상이 자동으로 이동합니다.</p></section>{loading ? null : profiles.length === 0 ? <p className="empty-state">아직 등록된 프로필이 없습니다. 새 프로필을 추가해주세요.</p> : null}<div className="profile-grid" aria-label="프로필 선택">{profiles.map((profile) => <div className="profile-tile" key={profile.id}><button className="profile-tile-select" type="button" onClick={() => onSelect(profile)} aria-label={`${profile.name} 프로필 선택`}><strong>{profile.name}</strong><span className="profile-tile-heights">앉음 {profile.sittingHeightCm.toFixed(1)}cm · 선 {profile.standingHeightCm.toFixed(1)}cm</span></button><button className="profile-tile-delete" type="button" onClick={(event) => void remove(profile, event)} aria-label={`${profile.name} 프로필 삭제`}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 7h14M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6m4-6v6" /></svg></button></div>)}<button type="button" className="add-profile-tile" onClick={onCreate}><span className="add-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span>새 프로필 추가</button></div><p className="status-message" role="status">{message}</p></main></>;
}

type BasicsProps = { name: string; onNameChange: (name: string) => void; onNext: () => void };

export function ProfileBasics({ name, onNameChange, onNext }: BasicsProps) {
  const [displayHeight, setDisplayHeight] = useState("170");
  const valid = name.trim().length > 0 && Number(displayHeight) >= 100 && Number(displayHeight) <= 230;
  return <><LegacyStyle css={profileFormCss} /><main><section className="page-heading"><p className="step-label">PROFILE SETUP</p><h1>사용자 프로필 설정</h1><p>맞춤형 책상 설정을 위해 정보를 입력해주세요.</p></section><form className="profile-form" onSubmit={(event) => { event.preventDefault(); if (valid) onNext(); }}><div className="fields"><label className="field"><span>사용자 이름</span><input value={name} onChange={(event) => onNameChange(event.target.value)} maxLength={30} autoComplete="name" placeholder="이름을 입력해주세요" required /></label><label className="field"><span>키</span><div className="input-unit"><input type="number" min="100" max="230" value={displayHeight} onChange={(event) => setDisplayHeight(event.target.value)} inputMode="numeric" placeholder="170" required /><span>cm</span></div><small>100~230 사이의 숫자를 입력해주세요.</small></label></div><section className="face-enrollment-panel" hidden><h2>얼굴 등록</h2></section><div className="form-footer"><p id="formMessage">{valid ? "입력이 완료되었습니다. 다음 단계로 이동할 수 있어요." : "이름과 키를 입력하면 다음 단계로 이동할 수 있어요."}</p><button id="nextButton" type="submit" disabled={!valid}>프로필 저장 <svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg></button></div></form></main></>;
}

type HeightsProps = { profile: Profile | null; name: string; onSaved: (profile: Profile) => void; onPrevious: () => void };

export function HeightSetup({ profile, name, onSaved, onPrevious }: HeightsProps) {
  const [sitting, setSitting] = useState(profile?.sittingHeightCm ?? 75);
  const [standing, setStanding] = useState(profile?.standingHeightCm ?? 100);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { setSitting(profile?.sittingHeightCm ?? 75); setStanding(profile?.standingHeightCm ?? 100); }, [profile]);
  const complete = async () => {
    setSaving(true);
    try {
      const saved = profile ? await updateProfile(profile.id, { name, sittingHeightCm: sitting, standingHeightCm: standing }) : await createProfile({ name, sittingHeightCm: sitting, standingHeightCm: standing, ledColor: null, ledBrightness: null, ledSchedule: null, tiltLevel: null, description: null });
      onSaved(saved);
    } catch (error) { setMessage(error instanceof Error ? error.message : "높이 설정을 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const sittingIcon = <svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="2.2"/><path d="M12 8.2v5.3m0 0H8.5m3.5 0 3.2 3.2M8.5 13.5v5M5.5 18.5h6"/></svg>;
  const standingIcon = <svg viewBox="0 0 24 24"><circle cx="12" cy="4" r="2.2"/><path d="M12 7.2v7m0-3.5-3.4 3.2M12 10.7l3.4 3.2M12 14.2l-2.7 6m2.7-6 2.7 6"/></svg>;
  return <><LegacyStyle css={deskSetupCss} /><main><section className="page-heading"><p className="step-label">DESK SETUP</p><h1>모션데스크 높이 설정</h1><p>편안한 자세에 맞는 책상 높이를 설정해주세요.</p></section><section className="height-grid" aria-label="자세별 책상 높이"><article className="height-card"><div className="card-top"><div className="posture-icon" aria-hidden="true">{sittingIcon}</div><span className="posture-number">01</span></div><p className="card-label">SITTING</p><h2>앉은 자세 높이</h2><div className="current-height"><span>현재 설정</span><strong>{sitting.toFixed(1)}<small>cm</small></strong></div><label className="height-field"><span>높이 직접 입력</span><div><input type="number" min={DESK_CONTROL_MIN_CM} max={DESK_CONTROL_MAX_CM} step="0.1" value={sitting} onChange={(event) => setSitting(Number(event.target.value))} /><span>cm</span></div></label><button className="save-height" type="button" onClick={() => setMessage(`앉은 자세 높이 ${sitting.toFixed(1)}cm를 입력했습니다.`)}>현재 높이 저장</button></article><article className="height-card"><div className="card-top"><div className="posture-icon" aria-hidden="true">{standingIcon}</div><span className="posture-number">02</span></div><p className="card-label">STANDING</p><h2>서 있는 자세 높이</h2><div className="current-height"><span>현재 설정</span><strong>{standing.toFixed(1)}<small>cm</small></strong></div><label className="height-field"><span>높이 직접 입력</span><div><input type="number" min={DESK_CONTROL_MIN_CM} max={DESK_CONTROL_MAX_CM} step="0.1" value={standing} onChange={(event) => setStanding(Number(event.target.value))} /><span>cm</span></div></label><button className="save-height" type="button" onClick={() => setMessage(`서 있는 자세 높이 ${standing.toFixed(1)}cm를 입력했습니다.`)}>현재 높이 저장</button></article></section><section className="automation-card"><div className="automation-heading"><div><p className="card-label">AUTOMATION</p><h2>자동 높이 조절</h2><p>설정한 시간이 지나면 자세에 맞춰 책상 높이를 자동으로 변경합니다.</p></div><label className="switch"><input type="checkbox" disabled /><span className="switch-track" /><span className="switch-label">자동 높이 조절 사용</span></label></div><div className="time-setting"><div><strong>자세 유지 시간</strong><p>오인식을 줄이기 위해 같은 자세를 5초 동안 확인합니다.</p></div><label><span className="sr-only">자세 유지 시간</span><select disabled><option>5초 (고정)</option></select></label></div></section><p className="status-message">{message}</p><nav className="page-actions"><a className="previous-button" href="#profile-form" onClick={(event) => { event.preventDefault(); onPrevious(); }}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></svg>이전</a><button className="complete-button" type="button" disabled={saving} onClick={() => void complete()}>설정 완료 <svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button></nav></main></>;
}
