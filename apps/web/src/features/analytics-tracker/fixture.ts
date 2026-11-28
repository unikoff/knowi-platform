import { BrowserTrackerController } from './controller';

// Synthetic only. This stub cannot issue network requests.
const output = document.querySelector<HTMLOutputElement>('#state')!;
const events = document.querySelector<HTMLPreElement>('#events')!;
const attempts = document.querySelector<HTMLPreElement>('#attempts')!;
const browserState = document.querySelector<HTMLOutputElement>('#browser-state')!;
const lifecycle = document.querySelector<HTMLPreElement>('#lifecycle')!;
const changes: Array<{ at: string; visibility: DocumentVisibilityState; focus: boolean; online: boolean }> = [];
const showBrowserState = () => {
  const state = { at: new Date().toISOString(), visibility: document.visibilityState, focus: document.hasFocus(), online: navigator.onLine };
  changes.push(state); lifecycle.textContent = JSON.stringify(changes, null, 2);
  browserState.textContent = `visibility=${state.visibility} focus=${state.focus} online=${state.online}`;
};
for (const name of ['visibilitychange', 'focus', 'blur', 'online', 'offline']) window.addEventListener(name, showBrowserState);
document.addEventListener('visibilitychange', showBrowserState);
showBrowserState();
let controller: BrowserTrackerController | null = null;
let course = 'fixture-one';
let deliveryFailure = false;
const received: Array<{ event_id: string; type: string; course_id: string; lesson_id: string | null; section: string | null; active_ms: number }> = [];
const deliveries: Array<{
  at: string;
  path: string;
  method: string | undefined;
  request: unknown;
  response: unknown;
}> = [];
function record(path: string, init: RequestInit | undefined, request: unknown, response: unknown) {
  deliveries.push({ at: new Date().toISOString(), path, method: init?.method, request, response });
  attempts.textContent = JSON.stringify(deliveries, null, 2);
}
const fetcher: typeof fetch = async (input, init) => {
  const path = new URL(String(input), location.href).pathname;
  if (path === '/api/analytics/grants/collect' && typeof init?.body === 'string') {
    const now = Date.now();
    record(path, init, JSON.parse(init.body), { status: 200 });
    return new Response(JSON.stringify({ grant: 'synthetic-grant', subject_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      server_time: new Date(now).toISOString(), expires_at: new Date(now + 300_000).toISOString() }), { status: 200 });
  }
  if (path !== '/api/analytics/events/batch' || typeof init?.body !== 'string') throw new Error('unexpected synthetic request');
  const body = JSON.parse(init.body) as { events: Array<{ event_id: string; type: string; course_id: string; lesson_id: string | null; section: string | null; active_ms: number }> };
  const online = navigator.onLine && !deliveryFailure;
  if (!online) {
    record(path, init, body, { error: 'synthetic offline' });
    throw new TypeError('synthetic offline');
  }
  received.push(...body.events.map(({ event_id, type, course_id, lesson_id, section, active_ms }) => ({ event_id, type, course_id, lesson_id, section, active_ms })));
  events.textContent = JSON.stringify(received, null, 2);
  const ack = { items: body.events.map((event, index) => ({ index, event_id: event.event_id, status: 'accepted', code: 'committed' })) };
  record(path, init, body, { status: 200, ack });
  return new Response(JSON.stringify(ack), { status: 200 });
};
function select(section: 'theory' | 'practice', lessonId = 'fixture-lesson') {
  controller?.onActivePanel({ id: section, kind: section, target: { trackId: 'fixture-track', lessonId } });
  output.textContent = lessonId === 'fixture-denied' ? 'denied' : section;
}
document.querySelector('#start')!.addEventListener('click', () => {
  controller?.stop();
  controller = new BrowserTrackerController({ identityKey: 'synthetic-only', subjectRef: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    role: 'user', fetcher, canTrackLesson: ({ lessonId }) => lessonId === 'fixture-lesson' });
  controller.start(); controller.setCourse({ courseId: course, catalogVersion: 'fixture-v1' }); select('theory');
});
document.querySelector('#theory')!.addEventListener('click', () => select('theory'));
document.querySelector('#practice')!.addEventListener('click', () => select('practice'));
document.querySelector('#denied')!.addEventListener('click', () => select('theory', 'fixture-denied'));
document.querySelector('#course')!.addEventListener('click', () => {
  course = course === 'fixture-one' ? 'fixture-two' : 'fixture-one';
  controller?.setCourse({ courseId: course, catalogVersion: 'fixture-v1' }); select('theory');
});
const transport = document.querySelector<HTMLButtonElement>('#transport')!;
transport.addEventListener('click', () => {
  deliveryFailure = !deliveryFailure;
  transport.setAttribute('aria-pressed', String(deliveryFailure));
  transport.textContent = deliveryFailure ? 'Восстановить отправку' : 'Ошибка отправки';
});
document.querySelector('#logout')!.addEventListener('click', () => { controller?.stop(); controller = null; output.textContent = 'Выход'; });
