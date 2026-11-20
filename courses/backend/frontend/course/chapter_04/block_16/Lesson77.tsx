import { useId, useState } from "react";
import {
  CodeBlock,
  Lead,
  LinkedNotes,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const inventoryExample = `import logging

from sqlalchemy import create_engine, text


def read_first_open(engine):
    statement = text(
        "SELECT id FROM inventory WHERE state = :wanted ORDER BY id LIMIT :size"
    )
    with engine.connect() as connection:
        return connection.execute(
            statement, {"wanted": "open", "size": 1}
        ).scalars().all()


def test_inventory_observation(caplog):
    name = "inventory_example"
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        hide_parameters=True,
        echo=False,
        logging_name=name,
    )
    logger_name = f"sqlalchemy.engine.Engine.{name}"
    previous_level = logging.getLogger(logger_name).level
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE inventory (id INTEGER, state TEXT)"))
            connection.execute(
                text("INSERT INTO inventory VALUES (:id, :state)"),
                [
                    {"id": 1, "state": "closed"},
                    {"id": 2, "state": "open"},
                    {"id": 3, "state": "open"},
                ],
            )
            total = connection.scalar(text("SELECT COUNT(*) FROM inventory"))

        caplog.clear()
        with caplog.at_level(logging.INFO, logger=logger_name):
            selected = read_first_open(engine)

        print("Подготовлено:", total)
        print("Ответ внутри окна:", selected)
        for record in caplog.records:
            if record.name == logger_name:
                print(record.getMessage())
        print("Уровень восстановлен:", logging.getLogger(logger_name).level == previous_level)

        caplog.clear()
        print("Ответ после окна:", read_first_open(engine))
        print("Сообщений после окна:", sum(r.name == logger_name for r in caplog.records))
    finally:
        engine.dispose()`;

const selectionStarter = `import logging
import re
from contextlib import closing
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.DBase.models import Base, TaskRow
from app.DBase.session import create_session_factory
from app.api import app


GET_PARAMS = {"is_done": "false", "sort_desc": "false", "limit": 1}
ROWS = (
    (11, "demo_title_a", "demo_tag_a", True),
    (23, "demo_title_b", "demo_tag_b", False),
    (41, "demo_title_c", "demo_tag_c", False),
)
HIDDEN_NOTICE = "[SQL parameters hidden due to hide_parameters=True]"


@pytest.fixture
def selection_lab(tmp_path):
    name = f"selection_{uuid4().hex}"
    database_url = URL.create("sqlite", database=(tmp_path / "selection.sqlite3").as_posix())
    engine = create_engine(
        database_url, hide_parameters=True, echo=False, logging_name=name
    )
    logger_name = f"sqlalchemy.engine.Engine.{name}"
    logger = logging.getLogger(logger_name)
    original_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        Base.metadata.create_all(engine)
        session_factory = create_session_factory(engine)
        with session_factory.begin() as session:
            session.add_all([
                TaskRow(
                    id=task_id, title=title, priority=2, tags=[tag],
                    title_normalized=title.lower(), is_done=is_done,
                )
                for task_id, title, tag, is_done in ROWS
            ])

        previous_factory = app.state.session_factory
        app.state.session_factory = session_factory
        try:
            with closing(TestClient(app)) as client:
                yield SimpleNamespace(
                    client=client, logger_name=logger_name, before=logger.level
                )
        finally:
            app.state.session_factory = previous_factory
    finally:
        logger.setLevel(original_level)
        engine.dispose()


def response_ids(response):
    if response.status_code != 200:
        pytest.fail(f"Ожидали HTTP 200, получен {response.status_code}.", pytrace=False)
    return [task["id"] for task in response.json()]


def show_observation(lab, caplog, response):
    if logging.getLogger(lab.logger_name).level != lab.before:
        pytest.fail("Уровень логгера не восстановился.", pytrace=False)

    messages = [record.getMessage() for record in caplog.records if record.name == lab.logger_name]
    markers = [value for _, title, tag, _ in ROWS for value in (title, tag)]
    if any(marker in message for marker in markers for message in messages):
        pytest.fail("В журнале появился тестовый маркер данных.", pytrace=False)

    safe_lines = []
    hidden = False
    for message in messages:
        if message in {"BEGIN (implicit)", "BEGIN", "COMMIT", "ROLLBACK"}:
            safe_lines.append(message)
        elif message.endswith(HIDDEN_NOTICE):
            hidden = True
            safe_lines.append("Параметры скрыты.")
        elif message.lstrip().upper().startswith("SELECT "):
            statement = " ".join(message.split())
            if not re.fullmatch(r"[A-Za-z0-9_., ?=*<>]+", statement):
                pytest.fail("SELECT содержит неожиданный фрагмент.", pytrace=False)
            safe_lines.append(statement)
        elif message.lstrip().upper().startswith(("PRAGMA ", "CREATE ", "INSERT ", "UPDATE ", "DELETE ")):
            safe_lines.append("В окне есть команда подготовки или записи.")
        else:
            pytest.fail("Журнал содержит открытые параметры или неожиданный формат.", pytrace=False)

    if not hidden:
        pytest.fail("В окне нет подтверждения сокрытия параметров.", pytrace=False)

    ids = response_ids(response)
    print(f"HTTP {response.status_code}, id: {ids}")
    print("\\n".join(safe_lines))
    print("Маркеров нет. Уровень логгера восстановлен.")


def test_selection_baseline(selection_lab, caplog):
    caplog.clear()
    response = selection_lab.client.get("/tasks", params=GET_PARAMS)
    assert response_ids(response) == [23]
    assert not any(record.name == selection_lab.logger_name for record in caplog.records)


def test_selection_observation(selection_lab, caplog):
    caplog.clear()
    response = None
    # Здесь добавим наблюдение за одним вызовом.
    if response is None:
        pytest.fail("Сначала добавим наблюдение за выборкой.", pytrace=False)
    show_observation(selection_lab, caplog, response)`;

const eventColumns = [
  { step: "Подготовка", call: "Загружаем предметы и проверяем их число.", sql: "CREATE, INSERT, контрольный SELECT" },
  { step: "Чтение", call: "Просим первый открытый предмет.", sql: "BEGIN, SELECT, ROLLBACK" },
  { step: "Ответ", call: "Получаем список [2].", sql: "Окно записи уже закрыто" },
];

function ObservationTimeline() {
  return (
    <figure style={{ margin: 0, width: "100%" }}>
      <figcaption style={{ marginBottom: 10, fontWeight: 600 }}>
        Вызов и журнал на одной шкале времени
      </figcaption>
      <ol style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 185px), 1fr))",
        gap: 10,
        listStyle: "none",
        padding: 0,
        margin: 0,
      }}>
        {eventColumns.map(({ step, call, sql }, index) => (
          <li key={step} style={{
            padding: "10px 14px",
            minWidth: 0,
            borderRadius: 8,
            border: index === 1 ? "1px solid currentColor" : "1px solid color-mix(in srgb, currentColor 28%, transparent)",
          }}>
            <strong>{step}</strong>
            <div>Вызов. {call}</div>
            <div>Журнал. {sql}.</div>
            {index === 1 && <div><strong>Окно записи</strong></div>}
          </li>
        ))}
      </ol>
    </figure>
  );
}

const logEvents = [
  { phase: 0, text: "CREATE, INSERT", select: false },
  { phase: 0, text: "SELECT COUNT(*) FROM inventory", select: true },
  { phase: 1, text: "BEGIN", select: false },
  { phase: 1, text: "SELECT id FROM inventory WHERE state = ? ORDER BY id LIMIT ?", select: true },
  { phase: 1, text: "Параметры скрыты.", select: false },
  { phase: 1, text: "ROLLBACK", select: false },
];

const windowChoices = [
  { label: "Перед подготовкой", reason: "Контрольный SELECT подготовки попал в журнал вместе с выборкой предмета." },
  { label: "Перед чтением", reason: "Подготовка уже закончилась. В журнал попала только выборка предмета. BEGIN и ROLLBACK обозначают границы чтения." },
  { label: "После чтения", reason: "К началу записи чтение уже завершилось, поэтому его SELECT в журнале отсутствует." },
];

const controlSize = { minHeight: 44, paddingInline: 8 };

function CaptureWindowDemo() {
  const controlId = useId();
  const [start, setStart] = useState(1);
  const [hasOpenRows, setHasOpenRows] = useState(true);
  const [prediction, setPrediction] = useState("");
  const [showResult, setShowResult] = useState(false);

  const captured = logEvents.filter((event) => event.phase >= start);
  const count = captured.filter((event) => event.select).length;
  const result = hasOpenRows ? "[2]" : "[]";

  function reset() {
    setStart(1);
    setHasOpenRows(true);
    setPrediction("");
    setShowResult(false);
  }

  return (
    <div style={{
      width: "100%",
      boxSizing: "border-box",
      border: "1px solid color-mix(in srgb, currentColor 28%, transparent)",
      borderRadius: 10,
      padding: 14,
      display: "grid",
      gap: 12,
    }}>
      <strong>Куда поставить начало записи?</strong>
      <div>Модель журнала. Инвентарь содержит предметы 1, 2 и 3. Читаем первый открытый предмет и закрываем запись после чтения.</div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 165px), 1fr))",
        gap: 10,
      }}>
        <div style={{ display: "grid", gap: 4 }}>
          <label htmlFor={`${controlId}-start`}>Начало окна</label>
          <select id={`${controlId}-start`} style={controlSize} value={start} onChange={(event) => {
            setStart(Number(event.target.value));
            setShowResult(false);
          }}>
            {windowChoices.map(({ label }, index) => <option key={label} value={index}>{label}</option>)}
          </select>
        </div>
        <div style={{ display: "grid", gap: 4 }}>
          <label htmlFor={`${controlId}-prediction`}>Сколько SELECT ожидаем?</label>
          <select id={`${controlId}-prediction`} style={controlSize} value={prediction} onChange={(event) => {
            setPrediction(event.target.value);
            setShowResult(false);
          }}>
            <option value="">Выберем число</option>
            <option value="0">0</option>
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
        </div>
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 36 }}>
        <input type="checkbox" checked={hasOpenRows} onChange={(event) => {
          setHasOpenRows(event.target.checked);
          setShowResult(false);
        }} />
        Предметы 2 и 3 открыты
      </label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <button type="button" style={controlSize} disabled={prediction === ""} onClick={() => setShowResult(true)}>Посмотреть журнал</button>
        <button type="button" style={controlSize} onClick={reset}>Сбросить</button>
      </div>
      <div aria-live="polite">
        {showResult && (
          <>
            <strong>{Number(prediction) === count ? "Прогноз совпал." : "Число отличается от прогноза."} SELECT: {count}. Ответ: <code>{result}</code>.</strong>
            <div>{windowChoices[start].reason}</div>
            {!hasOpenRows && <div>Список пуст, потому что открытых предметов нет. Чтение всё равно выполнилось.</div>}
            {captured.length > 0 && (
              <ul style={{ paddingInlineStart: 20, marginBottom: 0 }}>
                {captured.map(({ text }) => (
                  <li key={text}><code style={{ overflowWrap: "anywhere" }}>{text}</code></li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function Lesson77({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Надёжность и наблюдение SQL"}
        title="Наблюдаем SQL одной выборки"
        intro="Свяжем один ответ Planner с командой SQLite и соберём след, в котором значения параметров скрыты."
      />

      <Section number="00" title="Вступление">
        <Lead>Запрашиваем открытые задачи и получаем нужную запись. По ответу видим, что вернул Planner. Как узнать, какую работу при этом выполнила SQLite?</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Мы умеем сверить ответ выборки по возвращённым задачам." },
          { title: "Новый вопрос", description: "Как увидеть, выполнила ли фильтр сама база?" },
          { title: "Результат", description: "Соберём след одного запроса и найдём фильтр, порядок и лимит. Значения параметров останутся скрытыми." },
        ]} />
        <p>Сначала прочитаем одну команду в журнале. Затем ограничим запись временем одного вызова и сравним журнал с ответом.</p>
      </Section>

      <Section number="01" title="Что видно по одному SELECT">
        <Lead>В инвентаре предмет 1 закрыт, а 2 и 3 открыты. Чтобы получить первый открытый предмет, база отбросит закрытые, упорядочит остальные по id и оставит один. В результате увидим <code>[2]</code>.</Lead>
        <CodeBlock language="text" caption="Команда в журнале SQLite"
          code="SELECT id FROM inventory WHERE state = ? ORDER BY id LIMIT ?" />
        <p><code>WHERE</code> задаёт условие отбора, <code>ORDER BY</code> порядок, <code>LIMIT</code> число результатов. Знак <code>?</code> отмечает место, куда драйвер подставляет значение, переданное отдельно от SQL. Его называют связанным параметром. Например, статус и размер выборки приходят в драйвер отдельно.</p>
        <p>Один знак <code>?</code> ещё ничего не говорит о сокрытии. SQLAlchemy может записать значения в следующее сообщение. Мы заранее создаём диагностический <code>Engine</code> с <code>hide_parameters=True</code>. Форма команды остаётся доступна, а вместо списка значений SQLAlchemy пишет, что параметры скрыты.</p>
        <p>В SQL может остаться часть самого выражения, например <code>is_done = 0</code> для булева условия SQLite. Флаг скрывает отдельно связанные значения. Текст, вставленный прямо в SQL, и строки результата при уровне <code>DEBUG</code> он не скрывает. Для опыта берём вымышленные данные и уровень <code>INFO</code>.</p>
      </Section>

      <Section number="02" title="Где открыть и закрыть окно записи">
        <Lead>Пока готовим таблицу, SQLite выполняет свои команды. Если включить журнал заранее, они смешаются с чтением, которое мы хотим проверить.</Lead>
        <ObservationTimeline />
        <p><code>BEGIN</code> открывает транзакцию, а <code>ROLLBACK</code> здесь завершает ресурс чтения. Команду SELECT считаем один раз, даже если след занимает несколько строк и рядом записаны служебные события.</p>
        <p>Готовый инструмент pytest <code>caplog</code> собирает сообщения в памяти. Поднимаем уровень для конкретного логгера, то есть источника сообщений диагностического Engine. После выхода из <code>with</code> прежний уровень возвращается даже при ошибке. Новый ресурс чтения берёт соединение уже внутри окна. На занятом соединении изменение уровня может вступить в силу только после следующего получения соединения.</p>
        <CodeBlock caption="Форма короткого захвата" code={
          "with caplog.at_level(logging.INFO, logger=logger_name):\n    selected = read_first_open(engine)"
        } />
        <p><code>logging.INFO</code> делает SQL-команды видимыми, а <code>logger=logger_name</code> задаёт, чьи сообщения нужны. Строки с отступом работают при временном уровне. Настройку <code>echo</code> оставляем выключенной, чтобы Engine не выводил журнал отдельно.</p>
        <CaptureWindowDemo />
        <h3>Запускаемый пример с инвентарём</h3>
        <p>Весь опыт уже собран ниже. Подготовка создаёт одну таблицу в памяти, добавляет три предмета и проверяет их число. В <code>read_first_open</code> SQLite получает условие <code>:wanted</code> и предел <code>:size</code> отдельно от текста команды. <code>scalars().all()</code> собирает выбранные id в список. Читаем один раз внутри окна и ещё раз после него.</p>
        <p>Чтобы повторить опыт локально, сохраним код как <code style={{ overflowWrap: "anywhere" }}>test_inventory_observation.py</code> во временной папке. Из неё в окружении проекта запустим <code style={{ overflowWrap: "anywhere" }}>python -m pytest -q -s test_inventory_observation.py</code>. Флаг <code>-q</code> сокращает служебный вывод, а <code>-s</code> показывает строки <code>print</code>.</p>
        <CodeBlock caption="test_inventory_observation.py" code={inventoryExample} />
        <p>Сначала появится «Подготовлено: 3». В журнале после «Ответ внутри окна: [2]» увидим <code>BEGIN (implicit)</code>, один <code>SELECT</code>, отметку о скрытых параметрах и <code>ROLLBACK</code>. После выхода получим «Уровень восстановлен: True». Повторное чтение снова даст <code>[2]</code>, а «Сообщений после окна: 0» покажет, что запись выключена.</p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>В Planner уже есть выборка и короткая Session на запрос. Сначала проверим исходный ответ на временной базе. Затем сами выберем границы записи вокруг одного вызова этой выборки.</Lead>
        <LinkedNotes items={[
          { title: "Снимем журнал выборки", description: "Запишем временное окно в тесте и убедимся, что после одного GET уровень логгера восстановился." },
          { title: "Сопоставим с ответом", description: "Найдём в следе один SELECT с фильтром, порядком и лимитом. Сохраним обезличенную форму команды и выбранный id." },
        ]} />
        <p>Ответ показывает, какая задача вернулась. Окно связывает с ответом одну команду базы и позволяет заметить посторонние события подготовки.</p>
        <h3>Подготовка к запуску</h3>
        <p>Скопируем файл из блока ниже в <code style={{ overflowWrap: "anywhere" }}>project/tests/test_sql_selection_observation.py</code>. Если уже начали этот тест, сохраним свои изменения. Работаем из папки <code>project/</code> в окружении, где запускаются тесты Planner. Если зависимостей ещё нет, установим их привычной командой проекта <code style={{ overflowWrap: "anywhere" }}>python -m pip install -r requirements-dev.txt</code>.</p>
        <p>Файл создаёт таблицу и три тестовые задачи во временной SQLite. Задача 11 уже завершена, 23 и 41 открыты. <code>selection_lab.client</code> вызывает существующий API Planner. У <code>client.get</code> есть путь и именованный аргумент <code>params</code> для параметров запроса. Они уже собраны в <code>GET_PARAMS</code>. <code>selection_lab.logger_name</code> содержит имя источника журнала. Готовая сводка читает статус и id из ответа в <code>response</code>, проверяет защиту и показывает безопасные строки.</p>
        <p>В командах из практики <code>::</code> выбирает один тест из файла. <code>--show-capture=no</code> выключает показ захваченных сообщений при ошибке. <code style={{ overflowWrap: "anywhere" }}>--log-cli-level=WARNING</code> удерживает обычный вывод логов в терминале на уровне предупреждений. Диагностические строки покажет наша сводка.</p>
        <p>
          <a
            href={`data:text/plain;charset=utf-8,${encodeURIComponent(selectionStarter + "\n")}`}
            download="test_sql_selection_observation.py"
          >
            Скачать исходный тест
          </a>
        </p>
        <CodeBlock caption="tests/test_sql_selection_observation.py" code={selectionStarter} />
        <p>Сначала доступен <code style={{ overflowWrap: "anywhere" }}>test_selection_baseline</code>. Он проверяет ожидаемый ответ с выключенным журналом. Для своей записи работаем только в <code style={{ overflowWrap: "anywhere" }}>test_selection_observation</code>. Критерии готовности и команды запуска находятся в карточке практики.</p>
      </Section>
    </RichLesson>
  );
}
