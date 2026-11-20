import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  DiagramNote,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 16 · Надёжность, запросы и передача";

const contactDemo = `from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import String, create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)


class LearningError(Exception):
    pass


class TrackedSession(Session):
    closed = False

    def close(self):
        super().close()
        self.closed = True


def contacts(session):
    statement = select(Contact.id, Contact.name).order_by(Contact.id)
    return [tuple(row) for row in session.execute(statement)]


def observe(sessions):
    with sessions() as reader:
        return contacts(reader)


def demonstrate(fail_after_commit):
    with TemporaryDirectory() as folder:
        url = URL.create("sqlite", database=str(Path(folder) / "contacts.sqlite3"))
        engine = create_engine(url)
        sessions = sessionmaker(bind=engine, class_=TrackedSession)

        try:
            Base.metadata.create_all(engine)
            with sessions() as seed:
                with seed.begin():
                    seed.add(Contact(id=1, name="Склад"))

            print("До:", observe(sessions))
            try:
                with sessions() as writer:
                    with writer.begin():
                        writer.add(Contact(id=2, name="Пункт выдачи"))
                        writer.flush()
                        with writer.no_autoflush:
                            name = writer.scalar(
                                select(Contact.name).where(Contact.id == 2)
                            )
                        print("В транзакции:", name)
                        if not fail_after_commit:
                            raise LearningError("до commit")
                    raise LearningError("после commit")
            except LearningError as error:
                print("Учебная ошибка:", error)

            print("Session закрылась:", writer.closed)
            print("После ошибки:", observe(sessions))

            if not fail_after_commit:
                with sessions() as recovery:
                    with recovery.begin():
                        recovery.add(Contact(id=3, name="Мастерская"))
                print("После следующей записи:", observe(sessions))
        finally:
            engine.dispose()


if __name__ == "__main__":
    demonstrate(fail_after_commit=False)
    print()
    demonstrate(fail_after_commit=True)`;

const practiceStarter = `from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.core.engine import make_engine
from app.main import build_service


class ControlledWriteError(Exception):
    pass


class ObservedSession(Session):
    flushed_id = None
    closed = False

    def close(self):
        super().close()
        self.closed = True


@pytest.fixture
def rollback_sessions(tmp_path):
    url = URL.create("sqlite", database=str(tmp_path / "write-recovery.sqlite3"))
    engine = make_engine(url.render_as_string(hide_password=False))
    sessions = sessionmaker(bind=engine, class_=ObservedSession)
    try:
        Base.metadata.create_all(engine)
        with sessions() as seed:
            with seed.begin():
                seed.add_all([
                    TaskRow(
                        id=120, title="Сверить смету", priority=2, is_done=True,
                        tags=["учёт", "архив"], title_normalized="сверить смету",
                    ),
                    TaskRow(
                        id=121, title="Закрыть заявку", priority=1, is_done=False,
                        tags=["входящие"], title_normalized="закрыть заявку",
                    ),
                ])
        yield sessions
    finally:
        engine.dispose()


def read_rows(session):
    return {
        row.id: (
            row.title, row.priority, row.is_done, tuple(row.tags),
            row.title_normalized,
        )
        for row in session.scalars(select(TaskRow).order_by(TaskRow.id))
    }


def stop_before_commit(session, title):
    @contextmanager
    def write_scope():
        with session.begin():
            yield
            # Проверяем SQL внутри текущей транзакции без запуска нового flush.
            with session.no_autoflush:
                statement = select(TaskRow.id).where(TaskRow.title == title)
                flushed_id = session.execute(statement).scalar_one_or_none()
            if flushed_id is None:
                raise AssertionError("Ожидалась уже выполненная вставка")
            session.flushed_id = flushed_id
            raise ControlledWriteError("до commit")

    return write_scope


def test_post_commit_keeps_row(rollback_sessions):
    with rollback_sessions() as reader:
        before = read_rows(reader)

    with rollback_sessions() as writer:
        @contextmanager
        def stop_after_commit():
            with writer.begin():
                yield
            raise ControlledWriteError("после commit")

        planner = build_service(
            repository=SqlTaskRepository(writer), write_scope=stop_after_commit,
        )
        with pytest.raises(ControlledWriteError, match="после commit"):
            planner.add_task("Сохранено до ошибки", 3, tags=["пример"])

    assert writer.closed
    with rollback_sessions() as reader:
        after = read_rows(reader)

    assert {task_id: after[task_id] for task_id in before} == before
    added = [fields for task_id, fields in after.items() if task_id not in before]
    assert added == [
        ("Сохранено до ошибки", 3, False, ("пример",), "сохранено до ошибки"),
    ]`;

const contactOutput = `До: [(1, 'Склад')]
В транзакции: Пункт выдачи
Учебная ошибка: до commit
Session закрылась: True
После ошибки: [(1, 'Склад')]
После следующей записи: [(1, 'Склад'), (3, 'Мастерская')]

До: [(1, 'Склад')]
В транзакции: Пункт выдачи
Учебная ошибка: после commit
Session закрылась: True
После ошибки: [(1, 'Склад'), (2, 'Пункт выдачи')]`;

const moments = {
  beforeFlush: {
    label: "До flush",
    sent: false,
    saved: false,
    reason: "Ошибка возникла до INSERT. По отсутствию нового контакта нельзя доказать, что база отменила выполненную вставку.",
  },
  beforeCommit: {
    label: "После flush, до commit",
    sent: true,
    saved: false,
    reason: "Первая Session видела отправленную строку. Исключение вышло из блока транзакции, и rollback отменил INSERT.",
  },
  afterCommit: {
    label: "После commit",
    sent: true,
    saved: true,
    reason: "Транзакция уже подтвердилась. Ошибка прервала получение результата, а новая Session прочитала сохранённый контакт.",
  },
} as const;

type FailureMoment = keyof typeof moments;

function ContactRollbackLab() {
  const [moment, setMoment] = useState<FailureMoment>("beforeCommit");
  const [prediction, setPrediction] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [recovered, setRecovered] = useState(false);
  const result = moments[moment];

  function clearResult() {
    setRevealed(false);
    setRecovered(false);
  }

  function reset() {
    setMoment("beforeCommit");
    setPrediction("");
    clearResult();
  }

  return (
    <figure className="lesson-infographic lesson-diagram">
      <figcaption className="lesson-diagram-caption">Когда ошибка меняет результат?</figcaption>
      <p>Учебная модель. В базе уже есть «Склад». Мы пытаемся добавить «Пункт выдачи». Выберем момент ошибки и предскажем, что прочитает новая Session.</p>
      <form onSubmit={(event) => { event.preventDefault(); setRevealed(true); }}>
        <div style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 15rem), 1fr))" }}>
          <label style={{ display: "grid", gap: "0.25rem", minWidth: 0 }}>
            Момент ошибки
            <select
              value={moment}
              style={{ width: "100%", minHeight: "2.5rem" }}
              onChange={(event) => {
                setMoment(event.target.value as FailureMoment);
                setPrediction("");
                clearResult();
              }}
            >
              <option value="beforeFlush">До flush</option>
              <option value="beforeCommit">После flush, до commit</option>
              <option value="afterCommit">После commit</option>
            </select>
          </label>
          <label style={{ display: "grid", gap: "0.25rem", minWidth: 0 }}>
            Наш прогноз
            <select
              value={prediction}
              style={{ width: "100%", minHeight: "2.5rem" }}
              onChange={(event) => { setPrediction(event.target.value); clearResult(); }}
              required
            >
              <option value="">Выбрать результат</option>
              <option value="only-original">Только Склад</option>
              <option value="both">Склад и Пункт выдачи</option>
            </select>
          </label>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBlockStart: "0.75rem" }}>
          <button type="submit" style={{ minHeight: "2.5rem" }}>Прочитать новой Session</button>
          <button type="button" style={{ minHeight: "2.5rem" }} onClick={reset}>Сбросить</button>
        </div>
      </form>
      <div aria-live="polite">
        {revealed && (
          <>
            <p><strong>{(prediction === "both") === result.saved ? "Прогноз совпал." : "Результат отличается от прогноза."}</strong></p>
            <p>INSERT выполнен: {result.sent ? "да" : "нет"}. Новая Session: {result.saved ? "Склад, Пункт выдачи." : "Склад."}</p>
            <p>{result.reason}</p>
            {moment === "beforeCommit" && (
              <button type="button" style={{ minHeight: "2.5rem" }} onClick={() => setRecovered(true)}>Следующая запись</button>
            )}
            {recovered && <p>Новая операция сохранила «Мастерскую». После неё ещё одна Session прочитала «Склад» и «Мастерскую».</p>}
          </>
        )}
      </div>
    </figure>
  );
}

function CopyablePython({ code, filename }: { code: string; filename: string }) {
  const [notice, setNotice] = useState("");

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code + "\n");
      setNotice("Код скопирован.");
    } catch {
      setNotice("Можно раскрыть код ниже и скопировать его вручную.");
    }
  }

  return (
    <div>
      <button type="button" style={{ minHeight: "2.5rem" }} onClick={copyCode}>Скопировать {filename}</button>
      <span role="status" style={{ marginInlineStart: "0.5rem" }}>{notice}</span>
      <details>
        <summary>Показать содержимое {filename}</summary>
        <CodeBlock code={code} caption={filename} />
      </details>
    </div>
  );
}

export function Lesson76_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Проверяем отмену записи до commit"
        intro="Остановим запись в учебной базе после INSERT и проверим, что увидит следующая Session."
      />

      <Section number="00" title="Вступление">
        <Lead>Мы умеем безопасно воспроизвести недоступность хранилища на учебном адресе. Теперь SQLite доступна и Planner успевает отправить запись, прежде чем происходит ошибка.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Planner сохраняет задачи в SQLite. Отказы можно проверять на отдельной временной базе." },
          { title: "Новый вопрос", description: "Как доказать, что вставка уже дошла до базы, а после ошибки в таблице остались ровно прежние строки?" },
          { title: "Результат", description: "Свежая Session покажет прежние задачи, а следующий вызов записи сохранит новую задачу." },
        ]} />
        <p>У попытки записи и у сохранённых данных разные признаки. Сначала проследим место ошибки, затем прочитаем таблицу после завершения транзакции.</p>
      </Section>

      <Section number="01" title="Где остановить незавершённую запись">
        <Lead>В справочнике уже есть контакт «Склад». Попробуем добавить «Пункт выдачи» и прервём работу в выбранный момент.</Lead>
        <p>Внешний блок <code>with sessions() as writer</code> выделяет отдельную Session и закрывает её при выходе. Внутренний блок <code>with writer.begin()</code> ограничивает транзакцию. <code>flush()</code> выполняет SQL внутри этой транзакции. Обычный выход из <code>begin()</code> вызывает commit, а учебное исключение, вышедшее из блока до commit, приводит к rollback.</p>
        <p>Если остановиться раньше, до <code>flush()</code>, INSERT ещё не выполнен. Если поднять исключение после успешного выхода из <code>begin()</code>, строка уже подтверждена. Закрытие Session освобождает ресурсы в обеих ветках.</p>
        <Diagram caption="Два исхода попытки добавить контакт" orientation="vertical">
          <DiagramFlow label="От записи в транзакции до независимой проверки" orientation="vertical">
            <DiagramNode title="Первая Session открывает транзакцию">
              <p>В отдельном блоке begin() появляется новая строка.</p>
            </DiagramNode>
            <DiagramArrow label="flush отправляет INSERT" />
            <DiagramNode title="«Пункт выдачи» виден внутри транзакции">
              <p>Контакт пока можно отменить.</p>
            </DiagramNode>
            <DiagramArrow label="граница commit определяет исход" />
            <DiagramNode title="Следующая Session читает результат" shape="database">
              <p>После ошибки между flush и commit в таблице только «Склад».</p>
              <p>После ошибки за границей commit в таблице «Склад» и «Пункт выдачи».</p>
            </DiagramNode>
          </DiagramFlow>
          <DiagramNote title="Что проверяем по отдельности">
            <p>Успешный flush показывает состоявшийся INSERT. Независимое чтение показывает, какие строки сохранились.</p>
          </DiagramNote>
        </Diagram>
      </Section>

      <Section number="02" title="Что увидит новая Session">
        <Lead>Снимок таблицы сохраняет обычные значения до записи. Для сравнения откроем ещё одну Session к тому же файлу после закрытия первой.</Lead>
        <p>Пример подготавливает две отдельные временные базы с таблицей контактов. В каждой начинается с подтверждённой строки <code>(1, "Склад")</code>. После <code>flush()</code> запрос ищет имя второго контакта внутри транзакции. <code>no_autoflush</code> запрещает запросу самому отправить ожидающие изменения, поэтому находка подтверждает, что INSERT уже выполнился.</p>
        <p>В одном запуске исключение выходит из <code>begin()</code>, в другом возникает после выхода. Обработчик <code>except LearningError</code> стоит снаружи обоих блоков. После выхода <code>closed</code> показывает факт вызова <code>close()</code>. Для чтения <code>observe(sessions)</code> каждый раз запрашивает у фабрики новый экземпляр Session.</p>
        <p>Для запуска сохраним файл <code>contact_rollback_demo.py</code> под свободным именем в папке <code>project/</code> и выполним <code>python contact_rollback_demo.py</code> в окружении Planner. Скрипт сам создаёт схему, закрывает Engine и удаляет временный каталог. Для практики этот файл не понадобится.</p>
        <CopyablePython code={contactDemo} filename="contact_rollback_demo.py" />
        <CodeBlock code={contactOutput} language="text" caption="Ожидаемый вывод справочника" />
        <p>До commit новая Session читает только «Склад». После этого отдельная штатная транзакция сохраняет «Мастерскую». Во втором опыте «Пункт выдачи» остаётся после ошибки, поэтому автоматический повтор создания может добавить ещё одну строку.</p>
        <ContactRollbackLab />
      </Section>

      <Section number="03" title="Как собрать проверку Planner">
        <Lead>В Planner вызов <code>add_task(title, priority, tags=...)</code> выполняет вставку через репозиторий. Репозиторий сам вызывает <code>flush()</code>, а сервис вызывает переданный ему <code>write_scope</code>, когда начинается запись.</Lead>
        <p>Для обычной записи передаём <code>session.begin</code> как аргумент <code>write_scope</code>. Сервис сам вызовет метод. Вызов <code>session.begin()</code> со скобками открыл бы транзакцию прямо сейчас. Чтобы проверочное чтение не открыло транзакцию раньше записи, для чтения берём отдельную Session.</p>
        <p>Готовый адаптер <code>stop_before_commit(session, title)</code> возвращает вызываемый объект для <code>write_scope</code>. Он даёт репозиторию закончить вставку, ищет эту строку по заголовку внутри транзакции без autoflush и сохраняет её id в <code>session.flushed_id</code>. После этого внутренняя область получает <code>ControlledWriteError</code> и откатывается. Без уже выполненной вставки адаптер выдаст диагностическую ошибку, а отметка останется пустой.</p>
        <p>Назначение <code>pytest.raises(LearningError)</code> видно на коротком примере со справочником. В блоке только вызов, от которого ждём учебную ошибку. Если ошибка другого типа или её не было, pytest остановит проверку. Поскольку ошибка вышла из транзакции, pytest проверяет её уже после отката.</p>
        <CodeBlock caption="Проверка ожидаемой ошибки справочника" code={
          'def broken_import():\n    raise LearningError("учебный сбой")\n\nwith pytest.raises(LearningError):\n    broken_import()'
        } />
        <p>Готовая функция <code>read_rows(session)</code> возвращает словарь по id. Пять значений для каждого ключа идут по порядку: название, приоритет, отметка выполнения, кортеж тегов и нормализованное название. Кортеж сохраняет содержимое тегов отдельно от ORM-объекта.</p>
        <CodeBlock caption="Два снимка адресной книги" code={
          'before = {1: ("Склад",)}\nafter = {1: ("Склад",)}\nassert after == before\nassert after[1] == ("Склад",)'
        } />
        <p>В первом <code>assert</code> сравниваются и набор ключей, и все значения. Второй показывает, как проверить поля одной записи по ключу. В тесте Planner до следующей вставки сравним весь набор. Затем возьмём id из результата штатного вызова. SQLite может снова выдать id, который был выделен отменённой вставке, поэтому сравниваем содержимое в нужный момент.</p>
        <QuizCard
          question="Справочник показал «Пункт выдачи» внутри транзакции, но после ошибки в новой Session остался один «Склад». Какое наблюдение показывает, что проверка дошла до INSERT?"
          options={[
            "Первая Session нашла «Пункт выдачи» после flush запросом с отключённым autoflush.",
            "После исключения вызвался close.",
            "Вторая Session прочитала только «Склад».",
          ]}
          correctIndex={0}
          explanation="Без первого наблюдения отсутствие новой строки могло бы означать, что ошибка возникла ещё до вставки. Проверка требует и отметки записи, и независимого результата после неё."
        />
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>В своём Planner добавим один тест в <code>tests/test_write_recovery.py</code>. SQLite и две исходные задачи подготовит тестовая оснастка.</Lead>
        <LinkedNotes items={[
          { title: "Остановим незавершённую запись", description: "Увидим назначенный при flush id, ожидаемое исключение и факт закрытия Session." },
          { title: "Перечитаем и запишем снова", description: "Свежая Session подтвердит исходные строки. После отдельной успешной записи ещё одна Session прочитает новую задачу." },
        ]} />
        <p>Первое чтение даёт данные для сравнения после сбоя. Позднее чтение подтверждает, что штатная запись снова работает. Готовый контраст отдельно проверяет ошибку уже после commit.</p>

        <h3 id="write-recovery-starter">Стартовый файл проверки записи</h3>
        <p>Скопируем полный блок в новый <code style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>project/tests/test_write_recovery.py</code>. Если файл с таким именем уже есть, возьмём свободное имя с <code>test_</code> в начале и подставим его в команды. В файле есть временная база с подготовленными строками, параметры наблюдения, адаптер и отдельный готовый контраст. Свою функцию проверки добавим после контраста.</p>
        <CopyablePython code={practiceStarter} filename="test_write_recovery.py" />
        <p>Запустим готовый контраст из <code>project/</code> в тестовом окружении. На Windows можно заменить <code>python</code> на <code>{".\\.venv\\Scripts\\python.exe"}</code>, если окружение не активировано.</p>
        <CodeBlock language="text" caption="Запуск готового контраста" code="python -m pytest -q tests/test_write_recovery.py::test_post_commit_keeps_row" />
        <p>Ожидается один пройденный тест. Учебная SQLite создаётся под <code>tmp_path</code>. Приложение запускать не требуется.</p>
      </Section>
    </RichLesson>
  );
}
