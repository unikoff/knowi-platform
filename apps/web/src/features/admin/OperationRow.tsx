import type { Operation } from "./scopedApi";

const actionLabels: Readonly<Record<string, string>> = {
  "quota.grant": "Выдача занятий",
  "subscription.extend.course": "Продление общей подписки",
  "subscription.extend.platform": "Продление общей подписки",
  "subscription.reset": "Снятие общей подписки",
  "assignment.grant": "Назначение ведущего",
  "assignment.revoke": "Отзыв ведущего",
  "admin.role": "Изменение роли администратора",
  "slot.create": "Создание слота",
  "slot.archive": "Архивация слота",
  "slot.transfer": "Перенос слотов",
  "booking.book": "Запись на занятие",
  "booking.cancel": "Отмена записи",
};

export function OperationRow({ item, courseTitle }: { item: Operation; courseTitle?: string }) {
  const action = Object.prototype.hasOwnProperty.call(actionLabels, item.action) ? actionLabels[item.action] : "Другое действие";
  const scope = item.course_id ? (courseTitle || "Курс недоступен") : "платформа";
  const reason = item.anonymized ? "Данные удалены" : (item.reason ?? "Причина недоступна");
  return <p className="admin-row">{new Date(item.created_at).toLocaleString("ru-RU")} · {action} · {scope} · {reason}</p>;
}
