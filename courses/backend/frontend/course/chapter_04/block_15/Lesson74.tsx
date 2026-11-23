import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramGroup,
  DiagramNode,
  DiagramNote,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 15 · Полный SQL-путь и единое переключение Planner";

const rentalExample = String.raw`from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


@contextmanager
def visit(factory, label):
    print(f"{label}: open")
    try:
        with factory() as db:
            yield db
    finally:
        print(f"{label}: close")


def show_rental(db, rental_id):
    query = text("SELECT item FROM rentals WHERE id = :id")
    item = db.execute(query, {"id": rental_id}).scalar_one_or_none()
    if item is None:
        raise LookupError("Выдача не найдена")
    print(item)


def example(database):
    engine = create_engine(URL.create("sqlite", database=str(database)))
    factory = sessionmaker(bind=engine)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE rentals (id INTEGER PRIMARY KEY, item TEXT)"
            ))

        print("внести фонарь")
        with visit(factory, "C1") as db:
            with db.begin():
                db.execute(
                    text("INSERT INTO rentals VALUES (:id, :item)"),
                    {"id": 5, "item": "Фонарь"},
                )
            print("commit: выдача 5")

        print("показать 5")
        with visit(factory, "C2") as db:
            show_rental(db, 5)

        print("показать 9")
        try:
            with visit(factory, "C3") as db:
                show_rental(db, 9)
        except LookupError as error:
            print(error)

        command = "покзать"
        if command not in ("внести", "показать", "выход"):
            print("Неизвестная команда")
        print("выход")
    finally:
        engine.dispose()


with TemporaryDirectory() as directory:
    example(Path(directory) / "rental.sqlite")`;

const rentalOutput = String.raw`внести фонарь
C1: open
commit: выдача 5
C1: close
показать 5
C2: open
Фонарь
C2: close
показать 9
C3: open
C3: close
Выдача не найдена
Неизвестная команда
выход`;

const cliStarter = String.raw`def choose_context_factory(service, context_factory):
    from contextlib import nullcontext

    if context_factory is not None:
        return context_factory
    return lambda: nullcontext(service)


def execute_command(service, command, read=input, write=print, *, context_factory=None):
    normalized_command = command.strip().lower()
    if normalized_command == "exit":
        return False

    handlers = {
        "add": handle_add,
        "list": handle_list,
        "find": handle_find,
        "done": handle_done,
        "delete": handle_delete,
        "search": handle_search,
        "stats": handle_stats,
    }
    handler = handlers.get(normalized_command)
    if handler is None:
        write("Неизвестная команда.")
        return True

    context_factory = choose_context_factory(service, context_factory)
    handler(service, read=read, write=write)
    return True


def run(service=None, read=input, write=print, *, context_factory=None):
    context_factory = choose_context_factory(service, context_factory)
    with context_factory() as active_service:
        while True:
            show_menu(write)
            command = read("Команда: ")
            keep_running = execute_command(active_service, command, read=read, write=write)
            if not keep_running:
                break`;

const cliCheck = String.raw`import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.cli import run
from app.core.engine import make_engine
from app.DBase.models import Base, TaskRow
from app.DBase.session import create_session_factory
from app.main import sql_service_context

TITLE = "Проверить проект"
ROW = re.compile(r"\[([1-9]\d*)\] " + re.escape(TITLE) + r" \| приоритет: 3 \| .+")


class InputFailure(RuntimeError):
    pass


def require(condition, description):
    print(("OK" if condition else "ОШИБКА") + f": {description}")
    return bool(condition)


def trace(engine, inputs, expected_prompts):
    events = []
    output = []
    active = set()
    replies = iter(inputs)
    serial = 0

    class TrackedSession(Session):
        def __init__(self, **options):
            super().__init__(**options)
            nonlocal serial
            serial += 1
            self.number = serial
            active.add(self.number)
            events.append(f"open C{self.number}")

        def close(self):
            super().close()
            active.remove(self.number)
            events.append(f"close C{self.number}")

    def read(prompt):
        if prompt == "Команда: ":
            state = ",".join(f"C{n}" for n in sorted(active)) or "нет"
            events.append(f"menu: {state}")
        reply = next(replies)
        if reply == "{сбой}":
            if prompt != "Номер задачи: ":
                raise ValueError("Сбой должен произойти при запросе номера задачи.")
            raise InputFailure("Контрольный сбой ввода")
        if reply in ("{id}", "{нет id}"):
            match = next((ROW.fullmatch(line) for line in output if ROW.fullmatch(line)), None)
            if match is None:
                raise ValueError("add не вывел номер созданной задачи.")
            return str(int(match[1]) + (reply == "{нет id}"))
        return reply

    factory = sessionmaker(bind=engine, class_=TrackedSession)
    failed_inside_command = False
    try:
        run(read=read, write=output.append, context_factory=lambda: sql_service_context(factory))
    except InputFailure:
        failed_inside_command = True

    print(*events, sep="\n")
    seen_prompts = sum(event.startswith("menu:") for event in events)
    require(seen_prompts == expected_prompts, "Все ожидаемые приглашения меню показаны.")
    if seen_prompts != expected_prompts:
        raise ValueError("Число приглашений отличается от сценария.")
    return events, output, failed_inside_command


def ordinary_json(directory):
    json_path = directory / "probe.json"
    sqlite_path = directory / "json-launch.sqlite"
    url = URL.create("sqlite", database=str(sqlite_path)).render_as_string()
    env = dict(os.environ, JSON_PATH=str(json_path), DATABASE_URL=url, PYTHONIOENCODING="utf-8")
    launch = subprocess.run(
        [sys.executable, "-B", "-m", "app.main"],
        input=f"add\n{TITLE}\n3\nlist\nexit\n",
        capture_output=True, encoding="utf-8", env=env, timeout=15,
    )
    saved = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else []
    return require(
        launch.returncode == 0
        and launch.stdout.count(TITLE) == 2
        and len(saved) == 1 and saved[0]["title"] == TITLE
        and saved[0]["priority"] == 3 and not sqlite_path.exists(),
        "Обычный запуск сохранил и показал задачу через временный JSON.",
    )


def verify(directory, baseline):
    engine = make_engine(URL.create("sqlite", database=str(directory / "commands.sqlite")).render_as_string())
    try:
        Base.metadata.create_all(engine)
        if baseline:
            events, _, _ = trace(engine, ["fiind", "exit"], 2)
            return require(
                events == ["open C1", "menu: C1", "menu: C1", "close C1"],
                "Старт открывает одну Session ещё до первого приглашения.",
            )

        events, output, _ = trace(
            engine, ["add", TITLE, "3", " FiNd ", "{id}", "find", "{нет id}", "fiind", "exit"], 5
        )
        print(*[line for line in output if not line.startswith("Команды:")], sep="\n")
        correct_events = require(
            events == [
                "menu: нет", "open C1", "close C1", "menu: нет", "open C2", "close C2",
                "menu: нет", "open C3", "close C3", "menu: нет", "menu: нет",
            ],
            "Три распознанные команды получили разные Session, остальные вводы без Session.",
        )

        matching = [line for line in output if ROW.fullmatch(line)]
        correct_output = require(
            len(matching) == 2 and matching[0] == matching[1]
            and output.count("Задача не найдена.") == 1
            and output.count("Неизвестная команда.") == 1,
            "Два одинаковых результата записи и чтения, затем два разных сообщения об ошибке.",
        )
        with create_session_factory(engine)() as db:
            row_id = int(ROW.fullmatch(matching[0])[1]) if matching else 0
            row = db.get(TaskRow, row_id)
            saved = require(
                row is not None and row.title == TITLE and row.priority == 3 and not row.is_done,
                "После закрытия команд отдельное чтение SQLite видит задачу.",
            )

        print("Проверка исключения")
        error_events, error_output, failed = trace(engine, ["find", "{сбой}"], 1)
        closed_on_error = require(
            failed and error_events == ["menu: нет", "open C1", "close C1"]
            and not any(ROW.fullmatch(line) for line in error_output),
            "Контролируемое исключение вышло наружу после закрытия Session.",
        )
        json_ok = ordinary_json(directory)
        return all((correct_events, correct_output, saved, closed_on_error, json_ok))
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory() as scratch:
        if not verify(Path(scratch), args.baseline):
            raise SystemExit(1)`;

const rentalSteps = [
  {
    input: "покзать",
    session: null,
    outcome: "Неизвестная команда",
    reason: "По опечатке прокат не нашёл действие. Меню продолжило работу, ресурс не понадобился.",
  },
  {
    input: "показать 9",
    session: "C1",
    outcome: "Выдача не найдена",
    reason: "Поиск распознан. Отсутствие записи обнаружили только внутри действия, а контекст освободил Session при исключении.",
  },
  {
    input: "список",
    session: "C2",
    outcome: "[5] Фонарь",
    reason: "Список открыл отдельную Session к той же базе. Она прочитала существующую выдачу и закрылась до нового меню.",
  },
  {
    input: "выход",
    session: null,
    outcome: "Работа завершена",
    reason: "Выход останавливает цикл ещё до вызова действия. Новая Session не создана.",
  },
];

function CopyableCode({ code, caption }: { code: string; caption: string }) {
  const [feedback, setFeedback] = useState("");

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setFeedback("Код скопирован.");
    } catch {
      setFeedback("Выделите код в блоке и скопируйте его вручную.");
    }
  }

  return (
    <div style={{ minWidth: 0, display: "grid", gap: "var(--space-2)" }}>
      <div className="interactive-actions">
        <button type="button" className="interactive-secondary" onClick={copyCode}>
          Скопировать код
        </button>
        <span role="status">{feedback}</span>
      </div>
      <CodeBlock code={code} caption={caption} />
    </div>
  );
}

function RentalTrace() {
  const [position, setPosition] = useState(0);
  const [prediction, setPrediction] = useState<boolean | null>(null);
  const [lastPrediction, setLastPrediction] = useState<boolean | null>(null);
  const current = rentalSteps[position];
  const played = rentalSteps.slice(0, position);
  const last = played.at(-1);
  const completedSessions = played.filter((step) => step.session).length;

  function advance() {
    if (!current || prediction === null) return;
    setLastPrediction(prediction);
    setPrediction(null);
    setPosition(position + 1);
  }

  function reset() {
    setPosition(0);
    setPrediction(null);
    setLastPrediction(null);
  }

  return (
    <div className="quiz-card">
      <h3>Откроется ли Session?</h3>
      <p>Модель проката. В базе уже есть выдача № 5 «Фонарь». Для каждой команды выберем прогноз и сверим журнал.</p>
      {current && (
        <>
          <p>Следующая команда ({position + 1} из {rentalSteps.length}) <code>{current.input}</code></p>
          <div className="interactive-actions" role="group" aria-label="Прогноз открытия Session">
            <button
              type="button"
              aria-pressed={prediction === true}
              onClick={() => setPrediction(true)}
              className={prediction === true ? "interactive-primary" : "interactive-secondary"}
            >
              Откроется{prediction === true && <span aria-hidden="true">✓</span>}
            </button>
            <button
              type="button"
              aria-pressed={prediction === false}
              onClick={() => setPrediction(false)}
              className={prediction === false ? "interactive-primary" : "interactive-secondary"}
            >
              Останется закрытой{prediction === false && <span aria-hidden="true">✓</span>}
            </button>
          </div>
        </>
      )}
      <div className="interactive-actions">
        <button
          type="button"
          onClick={advance}
          disabled={!current || prediction === null}
          className="interactive-primary"
        >
          Следующая команда
        </button>
        <button
          type="button"
          onClick={reset}
          className="interactive-secondary"
        >
          Сбросить
        </button>
      </div>
      <div className="quiz-description" aria-live="polite" aria-atomic="true">
        <p>Открыто: {completedSessions}. Закрыто: {completedSessions}. Активных Session: 0.</p>
        {last && (
          <p>
            <strong>{lastPrediction === Boolean(last.session) ? "Прогноз совпал." : "Прогноз разошёлся с журналом."}</strong>{" "}
            {last.reason}
          </p>
        )}
      </div>
      {played.length > 0 && (
        <ol className="lesson-content-list">
          {played.map((step) => (
            <li key={step.input}>
              <code>{step.input}</code>{" · "}
              {step.session
                ? `open ${step.session} → действие «${step.outcome}» → close ${step.session}`
                : `${step.outcome}. Без Session.`}
            </li>
          ))}
        </ol>
      )}
      {!current && <p>Сценарий завершён. Для нового эксперимента нажмите «Сбросить».</p>}
    </div>
  );
}

export function Lesson74({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Открываем Session на одну команду CLI"
        intro="Распознанная команда получит свою Session. Следующая команда прочитает подтверждённую запись через новую."
      />

      <Section number="00" title="Почему Session живёт меньше меню">
        <Lead>После каждой команды Planner показывает меню и ждёт ввода. Иногда ждать приходится долго, хотя предыдущая работа с базой уже закончилась.</Lead>
        <LinkedNotes
          items={[
            {
              title: "Точка старта",
              description: "Знакомые команды уже вызывают PlannerService. SQL-репозиторий умеет выполнять операции Planner.",
            },
            {
              title: "Новый вопрос",
              description: "В какой момент дать команде Session и где закончить работу с ней?",
            },
            {
              title: "Результат",
              description: "Каждая распознанная команда получает свой ресурс. Следующая команда читает сохранённую запись через новую Session.",
            },
          ]}
        />
        <p>Мы сохраним привычные команды. Чтобы выбрать границу ресурса, проследим момент от распознавания ввода до возвращения в меню.</p>
      </Section>

      <Section number="01" title="От открытия до следующего меню">
        <Lead>Меню может ждать следующего ввода сколько угодно. Дадим доступ к базе только той команде, у которой уже есть работа.</Lead>
        <p>
          Фабрика контекста хранит способ получить ресурс. Переданное имя функции можно вызвать позже.
          В записи <code>with visit(factory, "C1") as db:</code> вызов создаёт контекст, а вход в него даёт новую Session.
          <code>as db</code> задаёт имя этой Session для тела блока. Отступ показывает, какая работа ещё пользуется ею.
          Когда тело заканчивается, контекст закрывает Session. Этот выход выполняется и при исключении.
        </p>
        <p>
          В примере проката команда «внести фонарь» должна сохранить выдачу, а следующая команда «показать 5» должна найти её.
          Для записи внутри Session есть отдельная область транзакции, <code>with db.begin()</code>.
          При успешном выходе из неё база подтверждает запись, то есть делает commit. Закрытие Session освобождает ресурс.
          Следующая Session видит запись, потому что она уже подтверждена в том же SQLite-файле.
        </p>
        <Diagram caption="Три команды на одной SQLite" orientation="vertical">
          <DiagramFlow label="От меню до коротких областей Session" orientation="vertical">
            <DiagramNode title="Меню проката" shape="terminator">Сначала определяем, нужна ли команда для работы с данными.</DiagramNode>
            <DiagramArrow label="внести фонарь" />
            <DiagramGroup title="C1 · Session для записи">
              <DiagramNode title="Сохраняем выдачу 5">Запись подтверждается внутри области транзакции. Затем C1 закрывается.</DiagramNode>
            </DiagramGroup>
            <DiagramArrow label="новый ввод · показать 5" />
            <DiagramGroup title="C2 · новая Session для чтения">
              <DiagramNode title="Читаем «Фонарь»">Строка уже в SQLite. После чтения C2 закрывается.</DiagramNode>
            </DiagramGroup>
            <DiagramArrow label="новый ввод · показать 9" />
            <DiagramGroup title="C3 · новая Session для чтения">
              <DiagramNode title="Выдача не найдена">Поиск завершается исключением. Контекст закрывает C3 до сообщения об ошибке.</DiagramNode>
            </DiagramGroup>
          </DiagramFlow>
          <DiagramNote title="Опечатка и выход">Ввод не запускает работу с данными, поэтому новой рамки Session нет.</DiagramNote>
        </Diagram>
        <p>
          Ниже весь пример для запуска. Он создаёт отдельный временный <code>rental.sqlite</code> с пустой таблицей.
          Одна фабрика направляет все три Session к этому файлу. Рабочая база Planner в примере не используется.
          Можно прочитать вывод или скопировать пример в свободный файл <code>lantern_rental_session.py</code> рядом с каталогом
          <code>app</code>. Из <code>project/</code> запустим <code>python lantern_rental_session.py</code> тем же интерпретатором, что и Planner.
        </p>
        <details className="quiz-card">
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Полный пример проката</summary>
          <CopyableCode code={rentalExample} caption="lantern_rental_session.py" />
        </details>
        <CodeBlock code={rentalOutput} caption="Терминал проката" language="text" />
        <p>
          В строках <code>open</code> и <code>close</code> одна и та же метка обозначает вход и выход одного ресурса.
          У второй команды метка C2. При поиске выдачи 9 сначала происходит <code>close</code>, затем внешний обработчик ошибки выводит сообщение.
        </p>
        <QuizCard
          question="Почему команда «показать 5» видит фонарь после закрытия C1?"
          options={[
            "Транзакция записала строку в SQLite до закрытия C1. C2 читает тот же файл.",
            "Закрытие C1 само записало строку в SQLite.",
            "C2 унаследовала объект выдачи из памяти C1.",
          ]}
          correctIndex={0}
          explanation="Commit произошёл внутри первой команды. На выходе из C1 освободили Session. Новая Session не использует прежний объект и читает подтверждённую строку из того же файла."
        />
      </Section>

      <Section number="02" title="Какие команды доходят до ресурса">
        <Lead>Опечатку можно заметить по введённой строке. Отсутствующую выдачу узнаем только после обращения к базе.</Lead>
        <RentalTrace />
        <p>
          В Planner при <code>exit</code> диспетчер возвращает <code>False</code>. Цикл <code>run</code> на этом заканчивается.
          Неизвестная команда даёт <code>True</code>: меню появится снова. Распознанный <code>find</code> уже выбрал действие.
          Ввод номера задачи относится к этому действию, поэтому он может происходить внутри ресурса.
          К следующему приглашению меню эта Session уже должна быть закрыта.
        </p>
        <p>
          В SQL-сборке <code>sql_service_context</code> выдаёт PlannerService с SQL-репозиторием.
          При записи сервис использует собственную область <code>write_scope</code>, как область транзакции в прокате.
          Граница команды отвечает за доступ к сервису и освобождение Session. Обычный HTTP-путь выдаёт ресурсы через
          <code>Depends</code>, а здесь точка входа в контекст находится в Python-коде CLI.
        </p>
      </Section>

      <Section number="03" title="Готовые материалы">
        <Lead>Работаем в своём Planner с полным SQL-репозиторием и готовым <code>sql_service_context</code>. Для наблюдения используем отдельную учебную SQLite.</Lead>
        <p>
          В <code>project/app/cli.py</code> заменим участок от <code>def execute_command(</code> до конца файла первым блоком.
          Меню и обработчики выше этого участка остаются на месте. Если в заменяемом участке есть свои правки, сохраним его копию в черновике редактора.
          Во втором блоке готовая самопроверка. Сохраним её как <code>check_cli_session.py</code> рядом с каталогом <code>app</code>.
          Если имя занято, используем свободное и подставим его в команды запуска.
        </p>
        <p>
          В стартовых сигнатурах <code>*</code> оставляет <code>context_factory</code> именованным аргументом.
          При SQL-запуске готового сервиса ещё нет, поэтому <code>service</code> может быть <code>None</code>.
          Готовый <code>choose_context_factory</code> возвращает переданную фабрику.
          При прежнем <code>run(service)</code> он даёт контекст, который выдаёт уже собранный JSON-сервис.
          Сам выбор фабрики Session не открывает. <code>read</code> и <code>write</code> обозначают функции ввода и вывода.
          В самопроверке эти функции подают заготовленные ответы и записывают вывод.
        </p>
        <details className="quiz-card">
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Стартовый фрагмент для app/cli.py</summary>
          <CopyableCode code={cliStarter} caption="Замена от def execute_command( до конца файла" />
        </details>
        <details className="quiz-card">
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Файл check_cli_session.py</summary>
          <CopyableCode code={cliCheck} caption="check_cli_session.py" />
        </details>
        <p>
          Из каталога <code>project/</code> выполним <code>python check_cli_session.py --baseline</code>.
          <code>--baseline</code> запускает только исходный опыт: опечатку <code>fiind</code> и <code>exit</code>.
          Найдём в стартере <code>with</code> и предскажем, что журнал увидит на первом приглашении меню.
        </p>
        <CodeBlock
          caption="Стартовый результат"
          language="text"
          code={"open C1\nmenu: C1\nmenu: C1\nclose C1"}
        />
        <p>
          <code>menu: C1</code> говорит, что при приглашении к вводу Session уже открыта. Она закрывается один раз после выхода из всего цикла.
          После своей правки запустим <code>python check_cli_session.py</code> без флага. Этот режим запишет задачу, прочитает её через другую Session,
          проверит два различающихся сообщения и закрытие при исключении. Строка <code>menu: нет</code> означает, что на этот момент Session нет.
          Проверка сама создаёт временные файлы SQLite и JSON. Рабочие настройки менять не нужно.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes
          items={[
            {
              title: "Ограничим ресурс одной командой",
              description: "В существующем CLI проведём фабрику от цикла к выбранному действию. Опечатка и выход перестанут открывать Session.",
            },
            {
              title: "Проверим следующий ресурс",
              description: "Добавим задачу на учебной SQLite и прочитаем тот же id в новой Session. Журнал покажет закрытие до следующего меню и при исключении.",
            },
          ]}
        />
        <p>
          Сверим запись и журнал вместе: вывод подтверждает результат операции, метки показывают срок жизни Session.
          Для выбора границы вернёмся к схеме проката, а для запуска к разделу «Готовые материалы».
          Проверенный учебный CLI пригодится для репетиции с API на общей тестовой базе.
        </p>
      </Section>
    </RichLesson>
  );
}
