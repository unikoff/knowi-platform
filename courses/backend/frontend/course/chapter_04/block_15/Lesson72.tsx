import { useState } from "react";
import {
  Callout,
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  Lead,
  LinkedNotes,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Глава 4 · Блок 15 · Полный SQL-путь и единое переключение";

const requestExample = `from pydantic import BaseModel
from sqlalchemy import JSON, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class RequestRow(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    rank: Mapped[int]
    published: Mapped[bool]
    labels: Mapped[list[str]] = mapped_column(JSON)
    name_key: Mapped[str]


class RequestedChanges(BaseModel):
    name: str | None = None
    rank: int | None = None
    published: bool | None = None


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
with Session(engine) as db, db.begin():
    db.add_all([
        RequestRow(id=12, name="Витрина", rank=4, published=True,
                   labels=["внутр"], name_key="витрина"),
        RequestRow(id=13, name="Архив", rank=1, published=False,
                   labels=["контроль"], name_key="архив"),
    ])

updates = []


@event.listens_for(engine, "after_cursor_execute")
def collect_updates(_connection, _cursor, sql, _params, _context, _many):
    if sql.lstrip().upper().startswith("UPDATE REQUESTS SET "):
        updates.append(sql)


def show(request_id, payload):
    updates.clear()
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    with Session(engine) as db, db.begin():
        row = db.get(RequestRow, request_id)
        if row is not None and changes:
            row.name = changes.get("name", row.name)
            row.rank = changes.get("rank", row.rank)
            row.published = changes.get("published", row.published)
            row.name_key = row.name.lower()

    with Session(engine) as check:
        row = check.get(RequestRow, request_id)
        if row is None:
            state = "строка отсутствует"
        else:
            state = (row.name, row.rank, row.published, list(row.labels), row.name_key)
        print(f"{request_id}: {state}; UPDATE={len(updates)}")


show(12, RequestedChanges(published=False, rank=None))
show(12, RequestedChanges())
show(99, RequestedChanges())
show(12, RequestedChanges(name="Летняя Выставка"))
show(13, RequestedChanges())
engine.dispose()`;

const requestOutput = `12: ('Витрина', 4, False, ['внутр'], 'витрина'); UPDATE=1
12: ('Витрина', 4, False, ['внутр'], 'витрина'); UPDATE=0
99: строка отсутствует; UPDATE=0
12: ('Летняя Выставка', 4, False, ['внутр'], 'летняя выставка'); UPDATE=1
13: ('Архив', 1, False, ['контроль'], 'архив'); UPDATE=0`;

// Public starter archive. It contains the service fragment and the local verification file.
const patchArchive = [
  "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",
].join("");

const patchInputs = {
  omitted: {
    label: "Пропустить флаг",
    payload: {},
    reason: "В теле нет ключа published. Исходный флаг сохранится.",
  },
  null: {
    label: "Передать null",
    payload: { published: null },
    reason: "Значение null отфильтровано. Исходный флаг сохранится.",
  },
  false: {
    label: "Передать false",
    payload: { published: false },
    reason: "False остался в выбранных полях и заменил прежний True.",
  },
  rank: {
    label: "Передать rank=2 и пропустить флаг",
    payload: { rank: 2 },
    reason: "Запись меняет ранг. Флаг остаётся таким, каким был.",
  },
} as const;

type PatchInput = keyof typeof patchInputs;
const patchInputKeys: PatchInput[] = ["omitted", "null", "false", "rank"];

function PatchPrediction() {
  const [input, setInput] = useState<PatchInput>("omitted");
  const [predictedFlag, setPredictedFlag] = useState("");
  const [predictedUpdates, setPredictedUpdates] = useState("");
  const [checked, setChecked] = useState(false);

  const current = patchInputs[input];
  const payload: { published?: boolean | null; rank?: number } = current.payload;
  const selected = Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== null));
  const actualFlag = selected.published ?? true;
  const actualRank = selected.rank ?? 4;
  const updateCount = Number(actualFlag !== true || actualRank !== 4);
  const flagLabel = actualFlag ? "True" : "False";
  const correct = predictedFlag === flagLabel && predictedUpdates === String(updateCount);
  const ready = Boolean(predictedFlag && predictedUpdates);

  function reset() {
    setInput("omitted");
    setPredictedFlag("");
    setPredictedUpdates("");
    setChecked(false);
  }

  return (
    <Callout tone="accent">
      <form
        aria-label="Прогноз результата PATCH"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) setChecked(true);
        }}
        style={{ display: "grid", gap: "0.75rem" }}
      >
        <h3>Предскажите, будет ли запись</h3>
        <p>Модель заявки на странице. Каждая попытка начинается с published=True и rank=4.</p>
        <label>
          Что передаёт запрос
          <select
            value={input}
            onChange={(event) => {
              setInput(event.target.value as PatchInput);
              setPredictedFlag("");
              setPredictedUpdates("");
              setChecked(false);
            }}
            style={{ display: "block", width: "100%", minHeight: "2.5rem" }}
          >
            {patchInputKeys.map((key) => (
              <option key={key} value={key}>{patchInputs[key].label}</option>
            ))}
          </select>
        </label>
        <p>Тело запроса <code>{JSON.stringify(payload)}</code></p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
          <label style={{ flex: "1 1 10rem" }}>
            Флаг после чтения
            <select
              value={predictedFlag}
              onChange={(event) => {
                setPredictedFlag(event.target.value);
                setChecked(false);
              }}
              style={{ display: "block", width: "100%", minHeight: "2.5rem" }}
              required
            >
              <option value="">Выберите значение</option>
              <option value="True">True</option>
              <option value="False">False</option>
            </select>
          </label>
          <label style={{ flex: "1 1 10rem" }}>
            Число UPDATE
            <select
              value={predictedUpdates}
              onChange={(event) => {
                setPredictedUpdates(event.target.value);
                setChecked(false);
              }}
              style={{ display: "block", width: "100%", minHeight: "2.5rem" }}
              required
            >
              <option value="">Выберите число</option>
              <option value="0">0</option>
              <option value="1">1</option>
            </select>
          </label>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
          <button type="submit" disabled={!ready}>Проверить</button>
          <button type="button" onClick={reset}>Сбросить прогноз</button>
        </div>
        <div role="status">
          {checked && (
            <>
              <strong>{correct ? "Прогноз совпал." : "Сравните прогноз с результатом."}</strong>
              <p>
                Выбранные поля <code>{JSON.stringify(selected)}</code>. Из строки читаем
                published={flagLabel}, rank={actualRank}. UPDATE: {updateCount}.
              </p>
              <p>{current.reason}</p>
              {!correct && <p>Для новой попытки передайте rank=2 без флага и проверьте оба результата.</p>}
            </>
          )}
        </div>
      </form>
    </Callout>
  );
}

export function Lesson72({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Применяем только переданные поля PATCH"
        intro="Перенесём частичное изменение Planner на SQLite и проверим, когда запрос действительно записывает данные."
      />

      <Section number="00" title="От замены к частичному изменению">
        <Lead>
          Через PUT мы уже можем сохранить новую версию задачи. Теперь клиент хочет
          поменять один признак, например снять отметку о завершении.
        </Lead>
        <LinkedNotes
          items={[
            { title: "Точка старта", description: "Находим задачу по id и сохраняем её новую версию." },
            { title: "Новый вопрос", description: "Как применить False, если другие поля пропущены или равны null?" },
            { title: "Результат", description: "Меняем выбранные поля. Пустой запрос возвращает найденную задачу без записи." },
          ]}
        />
        <p>
          Сначала проследим источники полей. Затем посмотрим, что будет в строке
          после запроса и сколько команд UPDATE дошло до SQLite.
        </p>
      </Section>

      <Section number="01" title="Откуда берутся значения">
        <Lead>
          В заявке «Витрина» указаны ранг 4 и флаг публикации True. Запрос снимает
          флаг и передаёт null для ранга. Что попадёт в сохранённую строку?
        </Lead>
        <p>
          В JSON <code>false</code> означает булево значение, а <code>null</code> Python читает
          как <code>None</code>. В нашем PATCH поле со значением null сохраняет старое значение.
          У пропущенного поля такой же итог. Явное false при этом меняет флаг.
        </p>
        <p>
          Маршрут Planner вызывает <code>payload.model_dump()</code> с двумя параметрами.
          Параметр <code>exclude_unset=True</code> исключает поля, которых не было во входе.
          Параметр <code>exclude_none=True</code> убирает поля со значением <code>None</code>.
          Явно переданное <code>False</code> остаётся. Проверка истинности значения потеряла бы это изменение.
        </p>
        <Diagram orientation="vertical" caption="Источники значений для заявки «Витрина»">
          <DiagramFlow orientation="vertical" label="От тела PATCH к полному состоянию заявки">
            <DiagramNode title="Тело запроса">
              <p><code>{'{"published": false, "rank": null}'}</code></p>
            </DiagramNode>
            <DiagramArrow label="Выбираем изменения" />
            <DiagramNode title="Выбранные поля">
              <p><code>published=False</code> пришёл из запроса.</p>
              <p><code>rank</code> исключён из-за null. Ключ <code>name</code> отсутствовал.</p>
            </DiagramNode>
            <DiagramArrow label="Берём остальные поля из найденной строки" />
            <DiagramNode title="Полное состояние заявки">
              <p>Имя «Витрина» · ранг 4 · флаг False · метка «внутр».</p>
            </DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          Попробуйте мысленно передать только <code>{'{"rank": 2}'}</code> исходной заявке.
          Сохранённый ранг станет 2, а флаг останется True. Старое значение берём
          из найденной заявки всякий раз, когда в словаре изменений нет ключа.
        </p>
        <p>
          Если словарь пуст, сервис всё равно проверяет id. Для найденной задачи
          он сразу возвращает её состояние. Если такого id нет, прежний обработчик
          отвечает 404. В обоих случаях UPDATE не требуется.
        </p>
      </Section>

      <Section number="02" title="Как увидеть запись">
        <Lead>
          Сначала возьмём одну заявку по id и изменим её флаг, затем перечитаем запись
          в новой Session и посмотрим на счётчик SQL.
        </Lead>
        <p>
          В коде ниже <code>db.get(RequestRow, request_id)</code> возвращает строку по ключу
          или None. <code>changes.get("published", row.published)</code> берёт значение
          из словаря и использует прежний флаг только при отсутствии ключа.
          После присваивания Session отслеживает изменения строки.
        </p>
        <p>
          Пример открывает собственную SQLite в памяти и сразу добавляет два
          синтетических ряда. При выходе из блока <code>db.begin()</code> Session
          отправляет накопленные изменения и подтверждает транзакцию. При исключении
          происходит откат. Следующая Session открывается после этого блока и читает
          сохранённые значения. Счётчик фиксирует UPDATE таблицы заявок.
        </p>
        <details>
          <summary>Полный пример patch_request_demo.py</summary>
          <p>
            Пример можно запустить отдельно как <code>python patch_request_demo.py</code>{" "}
            в привычном окружении Planner с Pydantic и SQLAlchemy. Файл создаёт базу
            в памяти из двух учебных записей.
          </p>
          <CodeBlock code={requestExample} />
        </details>
        <p>У каждой строки вывода есть id, состояние после повторного чтения и число записей.</p>
        <CodeBlock code={requestOutput} />
        <p>
          Первый запрос сохраняет False. Пустой запрос к той же заявке читает её
          и оставляет счётчик на нуле. В запросе к 99 строки нет. Переименование
          меняет имя и его ключ, вычисленный через <code>lower()</code>. Данные
          соседней заявки 13 сохраняются.
        </p>

        <PatchPrediction />

        <p>
          В Planner запись относится к уже найденной <code>TaskRow</code>.
          Сервис формирует проверенную Task с её id и тегами. Метод{" "}
          <code>update(self, task: Task) -&gt; Task | None</code> принимает
          это полное состояние. В случае успеха возвращается предметная Task,
          при отсутствии строки возвращается None. Запись PUT уже умеет обращаться
          к строке по id, сохранять tags, пересчитывать <code>title_normalized</code> и
          преобразовывать результат через <code>task_from_row(row)</code>.
        </p>
        <p>
          Репозиторий получает текущую Session. Его <code>flush()</code> отправляет
          накопленные команды в открытую транзакцию. Готовый{" "}
          <code>with self.write_scope():</code> в сервисе открывает область до первого
          <code> get_task</code> и подтверждает результат при успешном выходе.
          Так HTTP 200 следует за сохранением. Новая Session показывает, что увидит
          следующий запрос.
        </p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>
          Продолжаем свой Planner с работающим SQL PUT. Готовая оснастка отправляет
          запросы к тому же маршруту PATCH через временную SQLite.
        </Lead>
        <LinkedNotes
          items={[
            {
              title: "Сохраним частичное изменение",
              description: "Подключим готовую область и напишем SQL update. В свежей строке изменится флаг выбранной задачи.",
            },
            {
              title: "Сверим пустой запрос и переименование",
              description: "Пустой запрос покажет ноль UPDATE. После переименования новое чтение покажет заголовок и его ключ.",
            },
          ]}
        />
        <p>
          При неожиданном значении вернитесь к схеме источников полей. Если
          не сходится счётчик, проверьте, в какой ветви сервис доходит до записи.
        </p>
        <h3>Готовая подготовка</h3>
        <p>
          <a href={`data:application/zip;base64,${patchArchive}`} download="planner-patch-kit.zip">
            Скачать оснастку PATCH
          </a>
        </p>
        <p>
          В архиве <code>patch_scope.fragment.txt</code> содержит готовый метод{" "}
          <code>PlannerService.patch_task</code> для <code>app/service.py</code>.
          Второй файл <code>tests/test_sql_patch_lab.py</code> положите в каталог{" "}
          <code>tests/</code> своего проекта. В нём есть временная база, исходные
          задачи и вывод состояния до и после каждого запроса.
        </p>
        <p>
          Из каталога <code>project</code> выполните первую команду тем же
          интерпретатором, которым проверяли PUT.
        </p>
        <CodeBlock code={"python -m pytest -q -s tests/test_sql_patch_lab.py -k start"} />
        <p>
          <code>-m pytest</code> запускает pytest текущим интерпретатором.{" "}
          <code>-q</code> оставляет краткий итог, <code>-s</code> показывает снимки,
          а <code>-k start</code> выбирает входную проверку. В её выводе PUT должен
          вернуть 200, а новая Session показать заголовок «Проба PUT», ключ «проба put»,
          прежние id и теги.
        </p>
        <p>
          После успешного старта замените старый <code>patch_task</code> готовым
          фрагментом целиком, сохранив отступ внутри класса. Затем в{" "}
          <code>app/DBase/repositories.py</code> самостоятельно добавьте{" "}
          <code>SqlTaskRepository.update</code>. Для проверки своего метода запустите
          все сценарии.
        </p>
        <CodeBlock code={"python -m pytest -q -s tests/test_sql_patch_lab.py"} />
        <p>
          Сравните строки до и после. Число UPDATE показывает, сколько записей
          выполнил этот запрос. Снимок из новой Session подтверждает значения
          цели и неизменность соседней строки.
        </p>
      </Section>
    </RichLesson>
  );
}
