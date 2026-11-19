import { useEffect, useRef, useState } from "react";
import { useAuthSession } from "../auth/useAuthSession";
import { AuthScreen } from "../auth";
import { StartupScreen } from "../../app/StartupScreen";
import { courseUrls, isCourseId, normalizedBase } from "../../shared/course-paths";
import { acknowledgeReconciliation, ReconciliationRequired, command, read, type PlatformOverview, type CourseSummary, type Operation, type PlatformUser } from "./scopedApi";
import { moscowCalendarMonth } from "../../shared/lib/moscow-month";
import { OperationRow } from "./OperationRow";
import { PlatformActivity } from "./PlatformActivity";
import { PlatformAnalytics } from "./analytics-platform/PlatformAnalytics";
import { AdminOverviewDashboard } from "./overview/AdminOverviewDashboard";
import { useCourseTitles } from "../../shared/useCourseTitles";
import { useScopedResource } from "./useScopedResource";
import { watchMoscowDay } from "./moscowDay";
import "./admin.css";
import "./admin-scoped.css";
import "./platform-admin.css";

type Tab = "overview" | "courses" | "users" | "operations";
function roleLabel(value: string) { return ({user: "Пользователь", leader: "Ведущий", admin: "Администратор"} as Record<string, string>)[value] ?? "Роль недоступна"; }
const errorText = (e: unknown) => e instanceof Error ? e.message : "Операция недоступна";
function State({loading, error}: {loading: boolean; error: string}) { return <>{loading && <p role="status">Загружаем…</p>}{error && <p className="form-error" role="alert">{error}</p>}</>; }
export function PlatformAdminEntry() {
  const { user, checking, initialized, recheck, revision } = useAuthSession();
  if (!initialized || checking) return <StartupScreen />;
  if (!user) return <AuthScreen onAuth={recheck} onBack={() => { window.location.href = normalizedBase(import.meta.env.BASE_URL); }} />;
  if (user.role !== "admin" || !user.capabilities?.includes("platform.read")) return <main className="status-page"><p role="alert">Нет доступа к администрированию платформы.</p></main>;
  return <PlatformAdmin key={`${user.id}:${revision}:${user.auth_revision}:${user.account_ref ?? ""}`} actorRef={typeof user.auth_revision === "number" ? user.account_ref : undefined} authRevision={user.auth_revision} />;
}
function PlatformAdmin({ actorRef, authRevision }: { actorRef?: string; authRevision?: number }) {
  const titles = useCourseTitles();
  const selectionRevision = useRef(0);
  const [tab, setTab] = useState<Tab>("overview");
  const [revision, setRevision] = useState(0);
  const [overviewRefresh, setOverviewRefresh] = useState(0);
  useEffect(() => {
    if (tab !== "overview") return;
    return watchMoscowDay(() => setOverviewRefresh(value => value + 1));
  }, [tab]);
  const [cursor, setCursor] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<PlatformUser | null>(null);
  const [selectedCourse, setSelectedCourse] = useState("");
  const [overviewCourse, setOverviewCourse] = useState("");
  const [overviewCourseCursor, setOverviewCourseCursor] = useState("");
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(30);
  const [amount, setAmount] = useState(1);
  const [month, setMonth] = useState(() => { const d = moscowCalendarMonth(); return `${d.year}-${String(d.month).padStart(2, "0")}`; });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reconcile, setReconcile] = useState<ReconciliationRequired | null>(null);
  const [notice, setNotice] = useState("");
  const overview = useScopedResource<PlatformOverview>(tab === "overview" ? `/admin-platform/overview${overviewCourse ? `?${new URLSearchParams({course_id: overviewCourse})}` : ""}` : null, revision + overviewRefresh);
  const overviewCourses = useScopedResource<{ courses: CourseSummary[]; next_cursor: string | null }>(tab === "overview" ? `/admin-platform/courses?${new URLSearchParams(overviewCourseCursor ? {cursor: overviewCourseCursor} : {})}` : null, revision);
  const courses = useScopedResource<{ courses: CourseSummary[]; next_cursor: string | null }>(tab === "courses" || selected ? `/admin-platform/courses?${new URLSearchParams(cursor ? {cursor} : {})}` : null, revision);
  const users = useScopedResource<{ users: PlatformUser[]; next_cursor: string | null }>(tab === "users" && !selected ? `/admin-platform/users?${new URLSearchParams({ search, ...(cursor ? {cursor} : {}) })}` : null, revision);
  const operations = useScopedResource<{ operations: Operation[]; next_cursor: string | null }>(tab === "operations" ? `/admin-platform/operations?${new URLSearchParams(cursor ? {cursor} : {})}` : null, revision);
  async function perform(path: string, body: unknown, accountRef?: string) {
    if (busy) return; const selectionAtStart = selectionRevision.current; setBusy(true); setError(""); setReconcile(null); setNotice("");
    try {
      const response = await command<{ subscription_until?: string | null }>(path, body, accountRef, actorRef, `admin:${authRevision}`);
      setNotice(response.subscription_until ? `Общая подписка до ${new Date(response.subscription_until).toLocaleString("ru-RU")}` : "Сохранено.");
      setRevision((x) => x + 1);
      if (selected) {
        try { const updated = await read<PlatformUser>(`/admin-platform/users/${selected.id}`); if (selectionAtStart === selectionRevision.current) setSelected(updated); }
        catch { if (selectionAtStart === selectionRevision.current) setNotice("Действие выполнено, но карточку не удалось обновить. Обновите страницу перед следующим действием."); }
      }
    } catch (e) { if (e instanceof ReconciliationRequired) setReconcile(e); setError(errorText(e)); } finally { setBusy(false); }
  }
  const switchTab = (next: Tab) => { selectionRevision.current += 1; setTab(next); setSelected(null); setCursor(""); setReason(""); setSelectedCourse(""); setReconcile(null); setError(""); setNotice(""); };
  return <main className="admin-panel admin-scoped" aria-label="Администрирование платформы"><h1>Администрирование платформы</h1><a href={normalizedBase(import.meta.env.BASE_URL)}>К каталогу курсов</a><nav className="admin-tabs section-navigation" aria-label="Разделы платформы">{([ ["overview", "Обзор"], ["courses", "Курсы"], ["users", "Пользователи"], ["operations", "Журнал операций"] ] as const).map(([id,label]) => <button key={id} type="button" className={tab === id ? "is-active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => switchTab(id)}>{label}</button>)}</nav>
  {error && <p className="form-error" role="alert">{error}</p>}{reconcile && <button className="admin-scoped-button" type="button" onClick={() => { if (window.confirm("Вы сверили результат предыдущего действия с данными системы или администратором? Новое действие не будет отправлено автоматически.")) { acknowledgeReconciliation(reconcile); setReconcile(null); setError(""); } }}>Подтвердить, что результат сверен</button>}{notice && <p role="status">{notice}</p>}
  {tab === "overview" && <section className="platform-overview">
    <div className="platform-overview__filter">
      <label className="admin-scoped-field">Показатели курса
        <select className="admin-scoped-input" value={overviewCourse} onChange={(e) => setOverviewCourse(e.target.value)} disabled={overviewCourses.loading || !!overviewCourses.error}>
          <option value="">Все курсы</option>
          {overviewCourses.data?.courses.map((course) => <option key={course.course_id} value={course.course_id}>{titles[course.course_id] ?? course.course_id}</option>)}
        </select>
      </label>
      {overviewCourses.data?.next_cursor && <button className="admin-scoped-button" type="button" onClick={() => {setOverviewCourse(""); setOverviewCourseCursor(overviewCourses.data!.next_cursor!);}}>Следующие курсы</button>}
      {overviewCourseCursor && <button className="admin-scoped-button" type="button" onClick={() => {setOverviewCourse(""); setOverviewCourseCursor("");}}>К первым курсам</button>}
    </div>
    <State loading={overviewCourses.loading} error={overviewCourses.error}/>
    {import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED === "true" && <PlatformAnalytics actorRef={actorRef} courseId={overviewCourse} scopeLabel={overviewCourse ? titles[overviewCourse] ?? overviewCourse : "Все курсы"} courses={(overviewCourses.data?.courses ?? []).map(course => ({id: course.course_id, title: titles[course.course_id] ?? course.course_id}))} />}
    <State loading={overview.loading} error={overview.error}/>
    {overview.data && <>
      <PlatformActivity activity={overviewCourse ? null : overview.data.activity_today} scope={overviewCourse ? titles[overviewCourse] ?? overviewCourse : "Все курсы"} />
      <section className="admin-overview__group"><div className="admin-overview__group-heading"><h2>Вся платформа</h2></div><div className="admin-overview__group-metrics is-three"><div className="admin-overview__metric"><small>Пользователи</small><strong>{overview.data.users}</strong></div><div className="admin-overview__metric"><small>Активные общие подписки</small><strong>{overview.data.active_subscribers}</strong></div><div className="admin-overview__metric"><small>Курсы</small><strong>{overview.data.courses}</strong></div></div></section>
      {overview.data.dashboard ? <><p className="admin-note">{overviewCourse ? `Показатели курса: ${titles[overviewCourse] ?? overviewCourse}. Подписки общие для платформы.` : "Подробная статистика ниже учитывает учеников без администраторов."}</p><AdminOverviewDashboard data={overview.data.dashboard} hideSubscriptions={Boolean(overviewCourse)} hideActivity={Boolean(overviewCourse)} onOpenStudent={(id) => { const request = ++selectionRevision.current; void read<PlatformUser>(`/admin-platform/users/${id}`).then((person) => { if (request !== selectionRevision.current) return; setSelected(person); setCursor(""); setReason(""); setSelectedCourse(""); setTab("users"); }).catch((e) => { if (request === selectionRevision.current) setError(errorText(e)); }); }} /></> : <p className="admin-note">Подробные показатели недоступны.</p>}
    </>}

  </section>}
  {tab === "courses" && <section className="admin-scoped-content"><State loading={courses.loading} error={courses.error}/>{courses.data && <>{courses.data.courses.length === 0 && <p>Курсов нет.</p>}{courses.data.courses.map((course) => <article className="admin-accounts-panel" key={course.course_id}><h3>{titles[course.course_id] ?? course.course_id}</h3>{isCourseId(course.course_id) && <a href={`${courseUrls(import.meta.env.BASE_URL, course.course_id).learning}?admin=1`}>Открыть панель курса</a>}</article>)}{courses.data.next_cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor(courses.data!.next_cursor!)}>Следующие курсы</button>}{cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor("")}>Сначала</button>}</>}
    </section>}
  {tab === "users" && (selected ? <section className="admin-scoped-content"><button className="admin-scoped-button" type="button" onClick={() => {selectionRevision.current += 1; setSelected(null);}}>← К пользователям</button><div className="platform-detail"><h2>{selected.username}</h2><p>{selected.email} · {roleLabel(selected.role)}</p></div><section className="platform-detail"><h3>Общая подписка платформы</h3><p>Действует до: {selected.subscription.subscription_until ? new Date(selected.subscription.subscription_until).toLocaleDateString("ru-RU") : "не активна"}</p></section><section className="platform-detail"><h3>Занятия по курсам</h3>{selected.quotas.length ? selected.quotas.map((q) => <p key={`${q.course_id}:${q.year}:${q.month}`}>{titles[q.course_id] ?? q.course_id}, {q.month}.{q.year}: {q.remaining} из {q.granted}</p>) : <p>Балансы отсутствуют.</p>}
    </section><section className="platform-detail"><h3>Управление доступом и занятиями</h3><label className="admin-scoped-field">Причина <input className="admin-scoped-input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500}/></label><p className="admin-scoped-fields"><label className="admin-scoped-field">Дней <input className="admin-scoped-input" type="number" min="1" max="3650" value={days} onChange={(e) => setDays(Number(e.target.value))}/></label> <button className="admin-scoped-button admin-scoped-button--primary" type="button" disabled={busy || !reason.trim() || days < 1 || days > 3650} onClick={() => void perform(`/admin-platform/users/${selected.id}/subscription-extensions`, {days, reason: reason.trim()}, selected.account_ref)}>Продлить общую подписку</button> <button className="admin-scoped-button" type="button" disabled={busy || !reason.trim()} onClick={() => {if (window.confirm("Снять общую подписку для всей платформы?")) void perform(`/admin-platform/users/${selected.id}/subscription-reset`, {reason: reason.trim(), confirmed: true}, selected.account_ref);}}>Снять общую подписку</button></p>
    <p className="admin-scoped-fields"><label className="admin-scoped-field">Курс <select className="admin-scoped-input" value={selectedCourse} onChange={(e) => setSelectedCourse(e.target.value)}><option value="">Выберите курс</option>{courses.data?.courses.map((c) => <option key={c.course_id} value={c.course_id}>{titles[c.course_id] ?? c.course_id}</option>)}</select></label> {courses.data?.next_cursor && <button className="admin-scoped-button" type="button" onClick={() => {setCursor(courses.data!.next_cursor!); setSelectedCourse("");}}>Следующие курсы</button>}{cursor && <button className="admin-scoped-button" type="button" onClick={() => {setCursor(""); setSelectedCourse("");}}>К началу курсов</button>} <label className="admin-scoped-field">Месяц <input className="admin-scoped-input" type="month" value={month} onChange={(e) => setMonth(e.target.value)}/></label> <label className="admin-scoped-field">Занятий <input className="admin-scoped-input" type="number" min="1" max="100" value={amount} onChange={(e) => setAmount(Number(e.target.value))}/></label> <button className="admin-scoped-button admin-scoped-button--primary" type="button" disabled={busy || !selectedCourse || !month || !reason.trim() || amount < 1 || amount > 100} onClick={() => {const [year, m] = month.split("-").map(Number); void perform(`/admin/courses/${encodeURIComponent(selectedCourse)}/users/${selected.id}/quota-grants`, {year, month: m, add: amount, reason: reason.trim()}, selected.account_ref);}}>Выдать занятия</button></p>
    </section></section> : <section className="admin-scoped-content"><h2>Пользователи платформы</h2><label className="admin-scoped-field">Поиск <input className="admin-scoped-input" value={search} onChange={(e) => {setSearch(e.target.value); setCursor("");}}/></label><State loading={users.loading} error={users.error}/>{users.data && <>{users.data.users.length === 0 && <p>Пользователи не найдены.</p>}{users.data.users.map((u) => <button className="admin-account-card admin-scoped-user" key={u.id} type="button" onClick={() => {selectionRevision.current += 1; setCursor(""); setSelectedCourse(""); setReason(""); setSelected(u);}}><span className="admin-account-card__identity"><strong>{u.username}</strong><small>{u.email}</small></span><span className="admin-scoped-user__role">{roleLabel(u.role)}</span></button>)}{users.data.next_cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor(users.data!.next_cursor!)}>Следующие пользователи</button>}{cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor("")}>Сначала</button>}</>}</section>)}
  {tab === "operations" && <section className="admin-scoped-content"><h2>Журнал операций</h2><State loading={operations.loading} error={operations.error}/>{operations.data && <>{operations.data.operations.length === 0 && <p>Операций нет.</p>}{operations.data.operations.map((item) => <OperationRow key={`${item.command_id}:${item.action}:${item.created_at}`} item={item} courseTitle={item.course_id ? titles[item.course_id] : undefined} />)}{operations.data.next_cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor(operations.data!.next_cursor!)}>Следующие операции</button>}{cursor && <button className="admin-scoped-button" type="button" onClick={() => setCursor("")}>Сначала</button>}</>}</section>}
  </main>;
}
