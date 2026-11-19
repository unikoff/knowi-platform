import "./calendar.css";
import "./account-tabs.css";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { Award, BarChart3, CalendarDays, CreditCard, Settings } from "lucide-react";
import { CalendarPanel } from "./CalendarPanel";
import type { AccountActivityStatus } from "./useAccountSchedule";
import type { AccountStatisticsData } from "./AccountStatistics";
import { CourseStatisticsPanel } from "./CourseStatisticsPanel";
import { canShowCourseAnalytics, canShowPersonalMeasurements } from "./course-statistics-analytics";
import { AccountAchievements } from "./AccountAchievements";
import type { LearnerAnalyticsCatalog } from "./LearnerAnalytics";
import { AccountPayments, type AccountPaymentsData } from "./AccountPayments";
import { AccountSettings, type AccountSettingsData } from "./AccountSettings";
import type { UserProfile } from "../../shared/api/contracts";
import type { CalendarMonth, StudentSlot, QuotaStatus } from "../../shared/api/contracts/account";
import type { ToolId } from "../analytics-tracker/tracker";

const accountTabs = [
  { id: "statistics", label: "Статистика", icon: BarChart3 },
  { id: "booking", label: "Запись", icon: CalendarDays },
  { id: "payments", label: "Оплата", icon: CreditCard },
  { id: "settings", label: "Настройки", icon: Settings },
  { id: "achievements", label: "Достижения", icon: Award },
] as const;
type AccountTab = typeof accountTabs[number]["id"];

export function AccountPanel({
  user,
  calendarMonth,
  activityDays,
  measurementStatus,
  bookingSlots,
  bookingQuota,
  bookingLoading, bookingError, courseId, ownerName,
  onMonthChange,
  onBookSlot,
  onCancelSlot,
  onLogout,
  onUserChange,
  statisticsData,
  analyticsCatalog,
  paymentsData,
  settingsData,
  onCheckout,
  onToolChange,
}: {
  user: UserProfile;
  calendarMonth: CalendarMonth;
  activityDays: Set<string>;
  measurementStatus: AccountActivityStatus;
  bookingSlots: StudentSlot[];
  bookingQuota: QuotaStatus | null;
  bookingLoading: boolean; bookingError: string; courseId: string; ownerName: string | null;
  onMonthChange: (calendarMonth: CalendarMonth) => void;
  onBookSlot: (slotId: number) => Promise<void>;
  onCancelSlot: (slotId: number) => Promise<void>;
  onLogout: () => void;
  onUserChange: (user: UserProfile) => void;
  statisticsData: AccountStatisticsData;
  analyticsCatalog: LearnerAnalyticsCatalog;
  paymentsData?: AccountPaymentsData | null;
  settingsData?: AccountSettingsData | null;
  onCheckout?: () => void;
  onToolChange?: (tool: ToolId) => void;
}) {
  const [activeTab, setActiveTab] = useState<AccountTab>("statistics");
  const tabsId = useId();
  useEffect(() => { onToolChange?.(`account-${activeTab}`); }, [activeTab, onToolChange]);

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex = index;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % accountTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index + accountTabs.length - 1) % accountTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = accountTabs.length - 1;
    else return;
    event.preventDefault();
    setActiveTab(accountTabs[nextIndex].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
  }

  return (
    <section className={`account-panel account-dashboard${activeTab === "booking" ? " account-dashboard--booking" : ""}`} aria-label="Личный кабинет">
      <div className="account-tabs section-navigation" role="tablist" aria-label="Разделы личного кабинета">
        {accountTabs.map(({ id, label, icon: Icon }, index) => (
          <button key={id} type="button" role="tab" id={`${tabsId}-tab-${id}`}
            aria-selected={activeTab === id} aria-controls={`${tabsId}-panel-${id}`}
            tabIndex={activeTab === id ? 0 : -1} onClick={() => setActiveTab(id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </button>
        ))}
      </div>

      <div className="account-tab-panel account-tab-panel--booking" role="tabpanel" id={`${tabsId}-panel-booking`}
        aria-labelledby={`${tabsId}-tab-booking`} hidden={activeTab !== "booking"} tabIndex={0}>
        <CalendarPanel
          courseId={courseId} ownerName={ownerName} bookingLoading={bookingLoading} bookingError={bookingError}
          calendarMonth={calendarMonth}
          activityDays={activityDays}
          measurementStatus={measurementStatus}
          bookingSlots={bookingSlots}
          bookingQuota={bookingQuota}
          onMonthChange={onMonthChange}
          onBookSlot={onBookSlot}
          onCancelSlot={onCancelSlot}
        />
      </div>
      <div className="account-tab-panel" role="tabpanel" id={`${tabsId}-panel-statistics`}
        aria-labelledby={`${tabsId}-tab-statistics`} hidden={activeTab !== "statistics"} tabIndex={0}>
        {activeTab === "statistics" && <CourseStatisticsPanel key={`${user.id}:${user.account_ref}:${user.role}:${user.auth_revision}:${courseId}:${Boolean(user.has_active_subscription)}`} data={statisticsData} courseId={courseId} catalog={analyticsCatalog} hasActiveSubscription={user.has_active_subscription ?? false} analyticsEnabled={canShowCourseAnalytics(import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED, user.role)} personalMeasurementsEnabled={canShowPersonalMeasurements(import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED, import.meta.env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED, user.role)} actorRef={user.account_ref} role={user.role} onViewSubscription={() => setActiveTab("payments")} />}
      </div>
      <div className="account-tab-panel" role="tabpanel" id={`${tabsId}-panel-payments`}
        aria-labelledby={`${tabsId}-tab-payments`} hidden={activeTab !== "payments"} tabIndex={0}>
        <AccountPayments user={user} data={paymentsData} onCheckout={onCheckout} />
      </div>
      <div className="account-tab-panel" role="tabpanel" id={`${tabsId}-panel-settings`}
        aria-labelledby={`${tabsId}-tab-settings`} hidden={activeTab !== "settings"} tabIndex={0}>
        <AccountSettings user={user} data={settingsData} onLogout={onLogout} onUserChange={onUserChange} />
      </div>
      <div className="account-tab-panel" role="tabpanel" id={`${tabsId}-panel-achievements`}
        aria-labelledby={`${tabsId}-tab-achievements`} hidden={activeTab !== "achievements"} tabIndex={0}>
        <AccountAchievements data={statisticsData} />
      </div>
    </section>
  );
}
