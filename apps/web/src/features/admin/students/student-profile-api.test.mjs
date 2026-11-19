import assert from "node:assert/strict";
import test from "node:test";
import {createServer} from "vite";

const vite = await createServer({configFile: false, optimizeDeps: {noDiscovery: true, include: []}, server: {middlewareMode: true, ws: false}});
const {fetchStudentProfileDetails, monthDateRange} = await vite.ssrLoadModule("/src/features/admin/students/student-profile-api.ts");
test.after(() => vite.close());

const courseId = "backend";
const actorRef = "01234567-89ab-4cde-8fab-0123456789ab";
const target = {id: 7, account_ref: "11111111-2222-4333-8444-555555555555"};
const month = {year: 2026, month: 11};
const subscriptionUntil = "2027-01-10T17:30:00Z";
const payload = {course_id: courseId, user_id: target.id, account_ref: target.account_ref, period: month,
  subscription_until: subscriptionUntil, subscription_plan: "pro", bookings: [], next_cursor: null};
const reply = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {"Content-Type": "application/json"}});
const booking = (id, status = "booked") => ({id, status, starts_at: "2026-10-31T21:00:00Z", ends_at: "2026-10-31T21:30:00Z"});

test("the month request keeps account identities and Moscow boundary bookings across pages", async t => {
  const start = Array.from({length: 100}, (_, index) => booking(index + 1));
  const cancelled = booking(101, "cancelled");
  const mock = t.mock.method(globalThis, "fetch", async url => new URL(url, "http://localhost").searchParams.has("cursor")
    ? reply({...payload, bookings: [cancelled]})
    : reply({...payload, bookings: start, next_cursor: "next-month-page"}));
  const signal = new AbortController().signal;
  const result = await fetchStudentProfileDetails(courseId, target, actorRef, month, signal);
  assert.deepEqual(result, {bookings: [...start, cancelled], bookingsComplete: true, subscriptionUntil, subscriptionPlan: "pro"});
  assert.equal(mock.mock.callCount(), 2);
  for (const {arguments: [url, options]} of mock.mock.calls) {
    assert.equal(options.headers["X-Actor-Ref"], actorRef);
    assert.equal(options.headers["X-Account-Ref"], target.account_ref);
    assert.equal(options.signal, signal);
    const requested = new URL(url, "http://localhost");
    assert.equal(requested.pathname, "/api/admin/courses/backend/users/7/profile-details");
    assert.equal(requested.searchParams.get("year"), "2026");
    assert.equal(requested.searchParams.get("month"), "11");
  }
  assert.deepEqual(monthDateRange({year: 2028, month: 2}), {from: "2028-02-01", to: "2028-02-29"});
});

test("a mismatched or malformed page never populates the selected profile", async t => {
  const mock = t.mock.method(globalThis, "fetch", async () => reply(payload));
  for (const mismatch of [{course_id: "another"}, {user_id: 8}, {account_ref: actorRef}, {period: {year: 2026, month: 12}},
    {bookings: [booking(1, "visited")]}, {bookings: [{...booking(1), starts_at: "2026-10-31T20:59:00Z"}]}]) {
    mock.mock.mockImplementationOnce(async () => reply({...payload, ...mismatch}));
    await assert.rejects(fetchStudentProfileDetails(courseId, target, actorRef, month, new AbortController().signal), {kind: "invalid"});
  }
});

test("an expired search is distinct from a late response discarded after cancellation", async t => {
  const controller = new AbortController();
  const mock = t.mock.method(globalThis, "fetch", async () => reply({detail: "Select an account through course search"}, 403));
  mock.mock.mockImplementationOnce(async () => ({ok: true, status: 200, headers: new Headers({"Content-Type": "application/json"}),
    json: async () => {controller.abort(); return payload;}}));
  await assert.rejects(fetchStudentProfileDetails(courseId, target, actorRef, month, controller.signal), {name: "AbortError"});
  await assert.rejects(fetchStudentProfileDetails(courseId, target, actorRef, month, new AbortController().signal), {kind: "search-required"});
});

test("the pagination limit explicitly marks the collected records as incomplete", async t => {
  let index = 0;
  const mock = t.mock.method(globalThis, "fetch", async () => {
    const bookings = Array.from({length: 100}, () => booking(++index));
    return reply({...payload, bookings, next_cursor: `page-${index}`});
  });
  const result = await fetchStudentProfileDetails(courseId, target, actorRef, month, new AbortController().signal);
  assert.equal(mock.mock.callCount(), 10);
  assert.equal(result.bookings.length, 1000);
  assert.equal(result.bookingsComplete, false);
  assert.equal(result.subscriptionUntil, subscriptionUntil);
});
