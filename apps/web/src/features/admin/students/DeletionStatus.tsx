import type { DeletionTarget } from "./deleteStudentRequest";

export type DeletionView = { target: DeletionTarget; status: "checking" | "pending" | "unknown"; error?: string };
export function DeletionStatus({ state, onCheck, onBack }: { state: DeletionView; onCheck: () => void; onBack: () => void }) {
  return <section className="admin-profile" aria-label="Состояние удаления аккаунта">
    {state.status === "checking" ? <p className="admin-note" role="status">Проверяем удаление…</p> : <>
      {state.status === "pending" ? <p className="admin-note" role="status">Аккаунт удалён. Ожидаем подтверждения удаления статистики.</p> : <div className="form-error" role="alert">Результат удаления неизвестен. {state.error}</div>}
      {state.status === "pending" && state.error && <div className="form-error" role="alert">Не удалось проверить статус. {state.error}</div>}
      <p className="admin-note">Проверьте статус удаления позже.</p>
      {state.target.accountRef ? <button className="admin-back" type="button" onClick={onCheck}>Проверить статус удаления</button> : <p className="admin-note">Идентификатор учётной записи недоступен. Для проверки обратитесь к администратору.</p>}
      <button className="admin-back" type="button" onClick={onBack}>К аккаунтам</button>
    </>}
  </section>;
}
