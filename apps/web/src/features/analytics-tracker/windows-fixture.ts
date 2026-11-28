import { BrowserTrackerController } from './controller';
import { WindowTracker } from './windows-controller';
import { WindowOutbox, emptyPartition } from './windows-outbox';
import { WINDOWS_AUTH_BOUNDARY, windowId, type Ack, type Snapshot } from './windows';

const params = new URLSearchParams(location.search);
const subjectRef = params.get('learner') === 'B'
  ? 'd4444444-4444-4444-8444-444444444444' : 'c3333333-3333-4333-8333-333333333333';
type FixtureContext = 'theory' | 'practice' | 'catalog' | 'denied';
const initialContext = params.get('context') ?? 'theory';
if (initialContext !== 'theory' && initialContext !== 'practice' && initialContext !== 'catalog' && initialContext !== 'denied') {
  throw new Error('unexpected synthetic context');
}
const events: unknown[] = [];
let tracker: WindowTracker | undefined;
let controller: BrowserTrackerController | undefined;

function select(context: FixtureContext) {
  document.querySelector('#context')!.textContent = context;
  if (!controller) return;
  controller.onActiveTool();
  if (context === 'catalog') {
    controller.setCourse({ courseId: 'platform-tools', catalogVersion: null });
    controller.onActiveTool('catalog');
  } else {
    controller.setCourse({ courseId: 'backend', catalogVersion: 'backend-v1' });
    const section = context === 'practice' ? 'practice' : 'theory';
    controller.onActivePanel({
      id: section, kind: section, target: { trackId: 'backend', lessonId: context === 'denied' ? 'denied' : '71' },
    });
  }
}

select(initialContext);
document.querySelector('#start')!.addEventListener('click', () => {
  controller?.stop();
  controller = new BrowserTrackerController({
    identityKey: 'synthetic-window-user',
    subjectRef,
    role: 'user',
    toolsEnabled: initialContext === 'catalog',
    canTrackLesson: ({ lessonId }) => lessonId === '71',
    createWindows: source => {
      tracker = new WindowTracker({ source, subjectRef, observe: event => events.push(event) });
      return tracker;
    },
  });
  controller.start();
  select(initialContext);
});
document.querySelector('#stop')!.addEventListener('click', () => controller?.stop());

async function seed(snapshot: Snapshot, due: number, confirmed?: Ack) {
  const outbox = new WindowOutbox(() => indexedDB);
  try {
    await outbox.transact(() => {
      const key = { activity_date: snapshot.activity_date, slot_no: snapshot.slot_no };
      const writer = { writer_id: snapshot.writer_id, writer_generation: snapshot.writer_generation };
      const state = emptyPartition(subjectRef, writer);
      state.windows[windowId(key)] = {
        key, stats: snapshot.stats, versions: snapshot.catalog_versions, seq: confirmed ? snapshot.seq : snapshot.seq - 1,
        nextDue: due, dirty: !confirmed, confirmed,
      };
      return { state, result: null };
    });
  } finally { outbox.close(); }
}

Object.assign(window, {
  windowFixture: {
    inspect: () => tracker?.inspect(),
    events: () => events,
    select,
    seed,
    authBoundary: () => window.dispatchEvent(new Event(WINDOWS_AUTH_BOUNDARY)),
  },
});
