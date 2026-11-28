/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ANALYTICS_REPORTS_ENABLED?: string;
  readonly VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED?: string;
  readonly VITE_ANALYTICS_CALENDAR_CACHE_ENABLED?: string;
  readonly VITE_ANALYTICS_TOOLS_ENABLED?: string;
  readonly VITE_ANALYTICS_WINDOWS_ENABLED?: string;
}
