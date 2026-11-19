import { shiftDate, type CourseTimeValues } from "./course-statistics-model";

export type CalendarSessionIdentity = {accountId: string; sessionMarker: string} | {guest: true};
type Account = Extract<CalendarSessionIdentity, {accountId: string}>;
type Boundary = {generation: string; identity: CalendarSessionIdentity | null};
export type CalendarDay = {date: string; revision: string; uniqueVisitors: number} & {[K in keyof CourseTimeValues]: number};
type StoredDay = CalendarDay & {courseId: string; historyGeneration: string};
export type CalendarCacheLease = {generation: string; account: Account};
type History = {lease: CalendarCacheLease; days: CalendarDay[]; generation?: string};

const databaseName = "knowi-personal-calendar-verified-v1";
const boundaryKey = "session";
const courseKey = (courseId: string) => `course:${courseId}`;
const waitMs = 1200;
export const calendarRevision = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const sameAccount = (identity: CalendarSessionIdentity, account: Account) =>
  "accountId" in identity && identity.accountId === account.accountId && identity.sessionMarker === account.sessionMarker;

function openCache(): Promise<IDBDatabase | null> {
  return new Promise(resolve => {
    let done = false;
    const finish = (db: IDBDatabase | null) => {
      if (done) { db?.close(); return; }
      done = true;
      clearTimeout(timer);
      resolve(db);
    };
    const timer = setTimeout(() => finish(null), waitMs);
    try {
      const request = indexedDB.open(databaseName, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore("boundary");
        request.result.createObjectStore("days", {keyPath: ["courseId", "date"]});
      };
      request.onsuccess = () => finish(request.result);
      request.onblocked = request.onerror = () => finish(null);
    } catch { finish(null); }
  });
}

async function transaction<T>(fallback: T, run: (tx: IDBTransaction, set: (value: T) => void) => void): Promise<T> {
  const db = await openCache();
  if (!db) return fallback;
  return new Promise(resolve => {
    try {
      const tx = db.transaction(["boundary", "days"], "readwrite");
      let value = fallback, done = false;
      const finish = (result: T) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        db.close();
        resolve(result);
      };
      const timer = setTimeout(() => {
        try { tx.abort(); } catch { /* The transaction already ended. */ }
        finish(fallback);
      }, waitMs);
      tx.oncomplete = () => finish(value);
      tx.onabort = tx.onerror = () => finish(fallback);
      try { run(tx, result => { value = result; }); } catch { tx.abort(); }
    } catch { db.close(); resolve(fallback); }
  });
}

/** Run under the shared auth lock, before a request that can change the cookie. */
export function beginCalendarAuthTransition(): Promise<string | null> {
  return transaction<string | null>(null, (tx, set) => {
    const boundaries = tx.objectStore("boundary");
    const request = boundaries.get(boundaryKey);
    request.onsuccess = () => {
      const previous = request.result as Boundary | undefined;
      // A previous unsettled request could still deliver a Set-Cookie.
      if (previous?.identity === null) return;
      const generation = crypto.randomUUID();
      boundaries.clear();
      boundaries.put({generation, identity: null} satisfies Boundary, boundaryKey);
      tx.objectStore("days").clear();
      set(generation);
    };
  });
}

export function finishCalendarAuthTransition(generation: string, identity: CalendarSessionIdentity): Promise<boolean> {
  return transaction(false, (tx, set) => {
    const boundaries = tx.objectStore("boundary");
    const request = boundaries.get(boundaryKey);
    request.onsuccess = () => {
      const row = request.result as Boundary | undefined;
      if (row?.generation !== generation || row.identity !== null) return;
      boundaries.put({generation, identity} satisfies Boundary, boundaryKey);
      set(true);
    };
  });
}

export function calendarDayWindow(today: string) {
  return {from: shiftDate(today, -29), to: shiftDate(today, -2)};
}

function validDay(row: CalendarDay) {
  return calendarRevision(row.revision) && (row.uniqueVisitors === 0 || row.uniqueVisitors === 1)
    && [row.activeMs, row.theoryMs, row.practiceMs].every(n => Number.isSafeInteger(n) && n >= 0)
    && (!row.activeMs || row.uniqueVisitors === 1)
    && row.theoryMs + row.practiceMs === row.activeMs;
}

/** The caller holds the auth lock and has verified this account and course via Core. */
export function openCalendarHistory(account: Account, authSignal: string, courseId: string, today: string) {
  return transaction<History | null>(null, (tx, set) => {
    const boundaries = tx.objectStore("boundary"), days = tx.objectStore("days");
    const request = boundaries.get(boundaryKey);
    request.onsuccess = () => {
      const row = request.result as Boundary | undefined;
      if ((row && (!row.identity || typeof row.generation !== "string"))
        || (authSignal && authSignal !== `ready:${row?.generation}`)) return;
      const matching = row?.identity && sameAccount(row.identity, account);
      if (authSignal && !matching) return;
      const generation = matching ? row.generation : crypto.randomUUID();
      const lease = {generation, account};
      if (!matching) {
        days.clear();
        boundaries.clear();
        boundaries.put({generation, identity: account} satisfies Boundary, boundaryKey);
        set({lease, days: []});
        return;
      }
      const {from, to} = calendarDayWindow(today);
      const projection = boundaries.get(courseKey(courseId));
      const result: CalendarDay[] = [];
      projection.onsuccess = () => {
        const historyGeneration = calendarRevision(projection.result) ? projection.result : undefined;
        const cursor = days.openCursor();
        cursor.onsuccess = () => {
          const item = cursor.result;
          if (!item) { set({lease, days: result, generation: historyGeneration}); return; }
          const day: StoredDay = item.value;
          if (day.date < from || day.date > to) item.delete();
          else if (day.courseId === courseId && day.historyGeneration === historyGeneration && validDay(day)) {
            const {date, revision, activeMs, theoryMs, practiceMs, uniqueVisitors} = day;
            result.push({date, revision, activeMs, theoryMs, practiceMs, uniqueVisitors});
          }
          item.continue();
        };
      };
    };
  });
}

function matchesLease(row: Boundary | undefined, lease: CalendarCacheLease) {
  return !!row?.identity && row.generation === lease.generation && sameAccount(row.identity, lease.account);
}

export function calendarLeaseCurrent(lease: CalendarCacheLease, projection?: {courseId: string; generation: string}) {
  return transaction<boolean | null>(null, (tx, set) => {
    const boundaries = tx.objectStore("boundary");
    const request = boundaries.get(boundaryKey);
    request.onsuccess = () => {
      if (!matchesLease(request.result, lease)) { set(false); return; }
      if (!projection) { set(true); return; }
      const generation = boundaries.get(courseKey(projection.courseId));
      generation.onsuccess = () => set(generation.result === projection.generation);
    };
  });
}

/** Update covered days after a fresh report. A late report from a superseded
 * projection cannot revert the generation observed by another tab. */
export function updateCalendarHistory(lease: CalendarCacheLease, courseId: string, historyGeneration: string,
                                      dates: readonly string[], rows: readonly CalendarDay[], expectedGeneration?: string) {
  return transaction(false, (tx, set) => {
    const boundaries = tx.objectStore("boundary");
    const request = boundaries.get(boundaryKey);
    request.onsuccess = () => {
      if (!matchesLease(request.result, lease)) return;
      const projection = boundaries.get(courseKey(courseId));
      projection.onsuccess = () => {
        if (projection.result !== expectedGeneration && projection.result !== historyGeneration) return;
        boundaries.put(historyGeneration, courseKey(courseId));
        const store = tx.objectStore("days"), accepted = new Set(dates);
        const cursor = store.openCursor(IDBKeyRange.bound([courseId, ""], [courseId, "\uffff"]));
        cursor.onsuccess = () => {
          const item = cursor.result;
          if (item) {
            const day: StoredDay = item.value;
            if (day.historyGeneration !== historyGeneration || accepted.has(day.date)) item.delete();
            item.continue();
            return;
          }
          for (const day of rows) {
            if (accepted.has(day.date) && validDay(day)) store.put({...day, courseId, historyGeneration} satisfies StoredDay);
          }
          set(true);
        };
      };
    };
  });
}
