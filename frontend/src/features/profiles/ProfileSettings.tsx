import { useCallback, useEffect, useState } from "react";

import {
  type ActivityMode,
  type ActivityModeInput,
  type Profile,
  type ProfileInput,
  ApiError,
  createActivityMode,
  createProfile,
  deleteActivityMode,
  deleteProfile,
  getDeskStatus,
  getProfile,
  listActivityModes,
  listProfiles,
  updateActivityMode,
  updateProfile,
} from "../../api/dashboard";
import { DESK_CONTROL_MAX_CM, DESK_CONTROL_MIN_CM } from "../../config";
import { navigate } from "../../routes";
import "./profile-settings.css";

type Props = { pathname: string };
type Draft = { name: string; sittingHeightCm: string; standingHeightCm: string; ledColor: string };

const emptyDraft = (): Draft => ({ name: "", sittingHeightCm: "75", standingHeightCm: "100", ledColor: "" });
const profileDraft = (profile: Profile): Draft => ({
  name: profile.name,
  sittingHeightCm: String(profile.sittingHeightCm),
  standingHeightCm: String(profile.standingHeightCm),
  ledColor: profile.ledColor ?? "",
});
const modeDraft = (mode?: ActivityMode): Draft => mode ? {
  name: mode.name,
  sittingHeightCm: String(mode.sittingHeightCm),
  standingHeightCm: String(mode.standingHeightCm),
  ledColor: mode.ledColor ?? "",
} : emptyDraft();

function toInput(draft: Draft): ProfileInput | ActivityModeInput | null {
  const sittingHeightCm = Number(draft.sittingHeightCm);
  const standingHeightCm = Number(draft.standingHeightCm);
  if (!draft.name.trim() || !Number.isFinite(sittingHeightCm) || !Number.isFinite(standingHeightCm)) return null;
  return { name: draft.name.trim(), sittingHeightCm, standingHeightCm, ledColor: draft.ledColor || null };
}

function Header() {
  return <header className="settings-header"><button type="button" className="settings-brand" onClick={() => navigate("/")} aria-label="SMART DESK 홈"><span aria-hidden="true" />SMART DESK</button><button type="button" className="settings-home" onClick={() => navigate("/")}>대시보드로</button></header>;
}

export function ProfileSettings({ pathname }: Props) {
  const match = /^\/settings\/profiles\/([^/]+)$/.exec(pathname);
  return <div className="settings-page"><Header />{pathname === "/settings/profiles" ? <ProfileList /> : pathname === "/settings/profiles/new" ? <ProfileEditor create /> : match ? <ProfileEditor profileId={decodeURIComponent(match[1])} /> : <main className="settings-main"><h1>페이지를 찾을 수 없습니다.</h1><button type="button" onClick={() => navigate("/settings/profiles")}>프로필 목록으로</button></main>}</div>;
}

function ProfileList() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setProfiles(await listProfiles()); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 불러오지 못했습니다."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return <main className="settings-main"><section className="settings-heading"><div><p>PROFILE SETTINGS</p><h1>프로필 관리</h1><span>프로필을 선택해 설정을 확인하거나 수정합니다.</span></div><button type="button" className="settings-primary" onClick={() => navigate("/settings/profiles/new")}>새 프로필</button></section>{error && <p className="settings-error" role="alert">{error}</p>}<section className="settings-list" aria-label="프로필 목록">{profiles.length === 0 ? <p className="settings-empty">아직 등록된 프로필이 없습니다.</p> : profiles.map((profile) => <button type="button" className="settings-profile" key={profile.id} onClick={() => navigate(`/settings/profiles/${encodeURIComponent(profile.id)}`)}><strong>{profile.name}</strong><span>기본 · 앉기 {profile.sittingHeightCm.toFixed(1)}cm · 서기 {profile.standingHeightCm.toFixed(1)}cm</span></button>)}</section></main>;
}

function ProfileEditor({ create = false, profileId }: { create?: boolean; profileId?: string }) {
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [modes, setModes] = useState<ActivityMode[]>([]);
  const [loading, setLoading] = useState(!create);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [modeEditor, setModeEditor] = useState<ActivityMode | "new" | null>(null);
  const [modeValue, setModeValue] = useState<Draft>(emptyDraft);

  const loadModes = useCallback(async (id: string) => {
    try { setModes(await listActivityModes(id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "작업 모드를 불러오지 못했습니다."); }
  }, []);
  useEffect(() => {
    if (create || !profileId) return;
    let alive = true;
    void (async () => {
      try {
        const profile = await getProfile(profileId);
        if (!alive) return;
        setEditingProfile(profile); setDraft(profileDraft(profile)); await loadModes(profile.id);
      } catch (cause) { if (alive) setError(cause instanceof Error ? cause.message : "프로필을 불러오지 못했습니다."); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [create, loadModes, profileId]);

  const updateDraft = (key: keyof Draft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  const useCurrentHeight = async (key: "sittingHeightCm" | "standingHeightCm") => {
    setMessage(""); setError("");
    try {
      const status = await getDeskStatus();
      if (status.height.status !== "ONLINE" || status.height.heightCm === null) {
        setError("현재 높이는 ONLINE 상태의 최신 센서 값에서만 사용할 수 있습니다."); return;
      }
      updateDraft(key, String(status.height.heightCm));
      setMessage("현재 높이를 draft에만 복사했습니다. 저장 전에는 책상 설정이 바뀌지 않습니다.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "현재 높이를 확인하지 못했습니다."); }
  };
  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault(); setError(""); setMessage("");
    const input = toInput(draft);
    if (!input) { setError("이름과 유효한 앉기·서기 높이를 입력해주세요."); return; }
    setSaving(true);
    try {
      const profile = create ? await createProfile(input) : await updateProfile(editingProfile!.id, input);
      setEditingProfile(profile); setDraft(profileDraft(profile));
      if (create) { navigate(`/settings/profiles/${encodeURIComponent(profile.id)}`); return; }
      setMessage("기본 작업 모드를 저장했습니다. 현재 사용자, 제어 방식, 작업 모드와 책상은 변경하지 않습니다.");
      await loadModes(profile.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const removeProfile = async () => {
    if (!editingProfile) return;
    const confirmation = `‘${editingProfile.name}’ 프로필과 모든 custom 작업 모드를 삭제할까요? 현재 범위에서는 작업 모드만 cascade 삭제됩니다. 얼굴 등록과 Mem0 장기 기억의 완전 삭제는 아직 연결되지 않았습니다.`;
    if (!window.confirm(confirmation)) return;
    setError("");
    try { await deleteProfile(editingProfile.id); navigate("/settings/profiles"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 삭제하지 못했습니다."); }
  };
  const saveMode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editingProfile) return;
    const input = toInput(modeValue);
    if (!input) { setError("작업 모드 이름과 유효한 앉기·서기 높이를 입력해주세요."); return; }
    setSaving(true); setError("");
    try {
      if (modeEditor === "new") await createActivityMode(editingProfile.id, input);
      else if (modeEditor) await updateActivityMode(modeEditor.key, input);
      setModeEditor(null); await loadModes(editingProfile.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "작업 모드를 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const removeMode = async (mode: ActivityMode) => {
    if (!window.confirm(`‘${mode.name}’ 작업 모드를 삭제할까요? 다음 작업 모드 선택 또는 다음 session부터 삭제 결과가 적용됩니다.`)) return;
    try { await deleteActivityMode(mode.key); if (editingProfile) await loadModes(editingProfile.id); }
    catch (cause) { setError(cause instanceof ApiError && cause.status === 409 ? "현재 활성 작업 모드는 삭제할 수 없습니다." : cause instanceof Error ? cause.message : "작업 모드를 삭제하지 못했습니다."); }
  };

  if (loading) return <main className="settings-main"><p>프로필을 불러오는 중입니다.</p></main>;
  return <main className="settings-main"><section className="settings-heading"><div><p>{create ? "NEW PROFILE" : "PROFILE SETTINGS"}</p><h1>{create ? "새 프로필" : editingProfile?.name ?? "프로필"}</h1><span>이 화면의 편집 내용은 설정 API에만 저장되며 현재 책상 실행 상태를 바꾸지 않습니다.</span></div><button type="button" className="settings-secondary" onClick={() => navigate("/settings/profiles")}>목록으로</button></section>{error && <p className="settings-error" role="alert">{error}</p>}{message && <p className="settings-message" role="status">{message}</p>}<form className="settings-card" onSubmit={(event) => void saveProfile(event)}><h2>기본 작업 모드 <small>이름: 기본</small></h2><p>기본 작업 모드의 이름은 바꿀 수 없습니다. 아래 값만 저장합니다.</p><ProfileFields draft={draft} onChange={updateDraft} onUseCurrent={useCurrentHeight} /><div className="settings-actions"><button type="submit" className="settings-primary" disabled={saving}>{create ? "프로필 만들기" : "기본값 저장"}</button></div></form>{!create && editingProfile && <section className="settings-card settings-modes"><div className="settings-section-title"><div><h2>사용자 작업 모드</h2><p>작업 모드는 설정값입니다. 저장·삭제해도 현재 작업 모드나 LED, 책상은 즉시 바뀌지 않습니다.</p></div><button type="button" className="settings-primary" onClick={() => { setModeEditor("new"); setModeValue(emptyDraft()); }}>작업 모드 추가</button></div><div className="settings-mode-list">{modes.map((mode) => <div className="settings-mode" key={mode.key}><div><strong>{mode.name}</strong><span>{mode.kind === "DEFAULT" ? "기본 작업 모드" : "사용자 작업 모드"} · 앉기 {mode.sittingHeightCm.toFixed(1)}cm · 서기 {mode.standingHeightCm.toFixed(1)}cm · LED {mode.ledColor ? `#${mode.ledColor}` : "없음"}</span></div>{mode.editable ? <div><button type="button" onClick={() => { setModeEditor(mode); setModeValue(modeDraft(mode)); }}>수정</button><button type="button" className="settings-danger-text" onClick={() => void removeMode(mode)}>삭제</button></div> : <em>이름 고정</em>}</div>)}</div></section>}{modeEditor && <div className="settings-modal" role="dialog" aria-modal="true" aria-label="작업 모드 편집"><form className="settings-card settings-dialog" onSubmit={(event) => void saveMode(event)}><h2>{modeEditor === "new" ? "작업 모드 추가" : "작업 모드 수정"}</h2><ProfileFields draft={modeValue} onChange={(key, value) => setModeValue((current) => ({ ...current, [key]: value }))} /><div className="settings-actions"><button type="button" className="settings-secondary" onClick={() => setModeEditor(null)}>취소</button><button type="submit" className="settings-primary" disabled={saving}>저장</button></div></form></div>}{!create && <section className="settings-delete"><h2>프로필 삭제</h2><p>custom 작업 모드는 profile 삭제와 함께 cascade 삭제됩니다. 얼굴 등록과 Mem0 장기 기억 완전 삭제는 아직 이 화면/API에 연결되지 않았습니다.</p><button type="button" className="settings-danger" onClick={() => void removeProfile()}>프로필 삭제</button></section>}</main>;
}

function ProfileFields({ draft, onChange, onUseCurrent }: { draft: Draft; onChange: (key: keyof Draft, value: string) => void; onUseCurrent?: (key: "sittingHeightCm" | "standingHeightCm") => void }) {
  return <div className="settings-fields"><label>이름<input value={draft.name} maxLength={100} onChange={(event) => onChange("name", event.target.value)} required /></label><label>기본 LED 색상<input type="color" value={`#${draft.ledColor || "000000"}`} onChange={(event) => onChange("ledColor", event.target.value.slice(1).toUpperCase())} /><button type="button" className="settings-link" onClick={() => onChange("ledColor", "")}>색상 없음</button></label>{(["sittingHeightCm", "standingHeightCm"] as const).map((key) => <label key={key}>{key === "sittingHeightCm" ? "앉기 높이" : "서기 높이"}<div className="settings-height"><input type="number" min={DESK_CONTROL_MIN_CM} max={DESK_CONTROL_MAX_CM} step="0.1" value={draft[key]} onChange={(event) => onChange(key, event.target.value)} required /><span>cm</span>{onUseCurrent && <button type="button" onClick={() => void onUseCurrent(key)}>현재 높이 사용</button>}</div></label>)}</div>;
}
