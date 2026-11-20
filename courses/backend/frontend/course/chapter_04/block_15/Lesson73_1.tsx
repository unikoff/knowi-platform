import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramGroup,
  DiagramNode,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const catalog = [
  { id: 1, label: "ЧАЙ 50%" },
  { id: 2, label: "Чай 500" },
  { id: 3, label: "Чай_подарок" },
  { id: 5, label: "Чай 'сбор'" },
];

const catalogExample = `from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy import create_engine, func, select

engine = create_engine("sqlite://")
metadata = MetaData()
catalog = Table(
    "catalog", metadata,
    Column("id", Integer, primary_key=True),
    Column("label", String),
    Column("searchable_label", String),
)
titles = [(1, "ЧАЙ 50%"), (2, "Чай 500"),
          (3, "Чай_подарок"), (5, "Чай 'сбор'")]


def show_fragment(connection, raw_fragment):
    fragment = raw_fragment.strip().lower()
    if not fragment:
        print(repr(raw_fragment), [])
        return

    position = func.instr(catalog.c.searchable_label, fragment)
    statement = (
        select(catalog.c.id, position.label("position"))
        .where(position > 0)
        .order_by(catalog.c.id)
    )
    if fragment == "'":
        compiled = statement.compile(dialect=engine.dialect)
        print("SQL:", compiled)
        print("Параметры:", compiled.params)

    print(repr(raw_fragment), connection.execute(statement).all())


try:
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(catalog.insert(), [
            {"id": number, "label": title, "searchable_label": title.lower()}
            for number, title in titles
        ])
        print("Python:", "ЧАЙ V2".lower())
        print("SQLite:", connection.scalar(select(func.lower("ЧАЙ V2"))))
        for value in ("  ЧАЙ 50%  ", "%", "_", "'", "чай", "кофе", "   "):
            show_fragment(connection, value)
finally:
    engine.dispose()`;

const catalogResults = `Python: чай v2
SQLite: ЧАЙ v2
'  ЧАЙ 50%  ' [(1, 1)]
'%' [(1, 7)]
'_' [(3, 4)]
"'" [(5, 5)]
'чай' [(1, 1), (2, 1), (3, 1), (5, 1)]
'кофе' []
'   ' []`;

function matchesLike(text: string, fragment: string) {
  const letters = Array.from(text);
  let previous = [true, ...letters.map(() => false)];

  for (const token of ["%", ...Array.from(fragment), "%"]) {
    const current = [token === "%" && previous[0]];
    for (const [index, letter] of letters.entries()) {
      current.push(token === "%"
        ? previous[index + 1] || current[index]
        : previous[index] && (token === "_" || token === letter));
    }
    previous = current;
  }

  return previous[letters.length];
}

function CatalogSearchProbe() {
  const [rawFragment, setRawFragment] = useState("%");
  const [prediction, setPrediction] = useState<number[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [showPattern, setShowPattern] = useState(false);

  // Follow Python's whitespace set at the edges of the input.
  const fragment = rawFragment
    .replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "")
    .toLowerCase();
  const literalRows = catalog.map(({ id, label }) => {
    const searchable = label.toLowerCase();
    const offset = fragment ? searchable.indexOf(fragment) : -1;
    const position = offset < 0 ? 0 : Array.from(searchable.slice(0, offset)).length + 1;
    return { id, position };
  });
  const matchedIds = literalRows.filter(({ position }) => position > 0).map(({ id }) => id);
  const patternIds = fragment && showPattern
    ? catalog.filter(({ label }) => matchesLike(label.toLowerCase(), fragment)).map(({ id }) => id)
    : [];
  const difference = literalRows.find(({ id, position }) => prediction.includes(id) !== (position > 0));

  function changeFragment(value: string) {
    setRawFragment(value);
    setPrediction([]);
    setRevealed(false);
    setShowPattern(false);
  }

  return (
    <section
      aria-label="Проба поиска в витрине"
      style={{
        width: "100%", boxSizing: "border-box", padding: 16,
        background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 8,
      }}
    >
      <h3>Какие товары найдутся?</h3>
      <p>Введите фрагмент, отметьте предполагаемые совпадения и проверьте себя. Пустой выбор тоже можно проверить.</p>
      <label style={{ display: "grid", gap: 4, marginBlock: 12 }}>
        Фрагмент названия
        <input
          value={rawFragment}
          maxLength={80}
          onChange={(event) => changeFragment(event.target.value)}
          style={{
            boxSizing: "border-box", font: "inherit", width: "min(100%, 18rem)",
            background: "var(--bg, Canvas)", color: "inherit", padding: "6px 10px",
            border: "1px solid var(--border, GrayText)", borderRadius: 6,
          }}
        />
      </label>
      <fieldset style={{
        margin: "0 0 12px", padding: 8, minInlineSize: 0, border: "1px solid var(--border, GrayText)", borderRadius: 6,
      }}>
        <legend>Мой прогноз для буквального поиска</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 12rem), 1fr))", gap: 6 }}>
          {catalog.map(({ id, label }) => (
            <label key={id} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: 4 }}>
              <input
                type="checkbox"
                checked={prediction.includes(id)}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setPrediction((ids) => (checked ? [...ids, id] : ids.filter((item) => item !== id)));
                  setRevealed(false);
                  setShowPattern(false);
                }}
              />
              <span>{id}. {label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <button type="button" onClick={() => setRevealed(true)}>Проверить прогноз</button>
        <button type="button" onClick={() => changeFragment("%")}>Сбросить пробу</button>
      </div>
      <div aria-live="polite">
        {revealed && (
          <>
            <p>
              <strong>Буквальные совпадения: [{matchedIds.join(", ")}].</strong>
              {" "}Фрагмент после обработки: <code>{JSON.stringify(fragment)}</code>.
            </p>
            {!fragment ? (
              <p>После удаления краевых пробелов фрагмент пуст. Поиск завершился до проверки строк.</p>
            ) : (
              <p>
                Позиции по id: {literalRows.map(({ id, position }) => id + " → " + position).join(", ")}.
                {" "}Ноль означает, что фрагмента в строке нет.
              </p>
            )}
            <p>
              {difference
                ? "Сверьте id " + difference.id + ": " + (difference.position
                  ? "фрагмент начинается в позиции " + difference.position
                  : "эта строка не подошла") + "."
                : "Ваш прогноз совпал с результатом."}
            </p>
            {fragment && (
              <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <input
                  type="checkbox"
                  checked={showPattern}
                  onChange={(event) => setShowPattern(event.target.checked)}
                />
                Сравнить с LIKE
              </label>
            )}
            {fragment && showPattern && (
              <p>
                <strong>Совпадения по шаблону: [{patternIds.join(", ")}].</strong>{" "}
                {fragment.includes("%") || fragment.includes("_")
                  ? "В шаблоне % разрешает любую последовательность, а _ разрешает один символ. Поэтому знак из запроса может совпасть с текстом, где этого знака нет."
                  : "Краевые % в шаблоне разрешают текст до и после фрагмента. В самом фрагменте сейчас обычные символы."}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export function Lesson73_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь и единое переключение Planner"}
        title="Ищем буквальный фрагмент названия в SQL"
        intro="Перенесём знакомый поиск Planner в SQLite. Проверим, что русские буквы, кавычка, процент и подчёркивание дают ожидаемые совпадения."
      />
      <Section number="00" title="От списка к поиску">
        <Lead>В списке задач есть название «Сверка 50%». Пользователь вводит знак процента и ожидает увидеть задачу с этим знаком в названии.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "У строк SQLite есть нормализованные названия. Условие SELECT позволяет выбрать нужные строки." },
          { title: "Новый вопрос", description: "Как найти кавычку, процент или подчёркивание как обычные знаки и учесть регистр русских букв?" },
          { title: "Результат", description: "Поиск возвращает буквальные совпадения по id, а пустой фрагмент даёт пустой список." },
        ]} />
        <p>Сначала приведём фрагмент к тому же виду, что и поисковую копию названия. Затем передадим его запросу как значение.</p>
      </Section>

      <Section number="01" title="Название и его поисковая копия">
        <p>
          Когда мы вводим «  ЧАЙ 50%  », <code>strip()</code> убирает краевые пробелы, сохраняя пробел
          между словами. Затем Python <code>lower()</code> превращает ввод в «чай 50%». Подготовленное
          поле <code>title_normalized</code> хранит копию исходного названия после Python <code>lower()</code>.
          В выдаче пользователь по-прежнему видит исходное название.
        </p>
        <Diagram caption="Что сравниваем и что показываем" orientation="vertical">
          <DiagramGroup title="Данные для сравнения">
            <DiagramFlow label="Название в витрине" orientation="vertical">
              <DiagramNode title="Исходное название"><code>ЧАЙ 50%</code> показываем в выдаче</DiagramNode>
              <DiagramArrow label="Python lower()" />
              <DiagramNode title="Поисковая копия"><strong>чай 50%</strong></DiagramNode>
            </DiagramFlow>
            <DiagramFlow label="Фрагмент от пользователя" orientation="vertical">
              <DiagramNode title="Ввод с пробелами"><code>{"  ЧАЙ 50%  "}</code></DiagramNode>
              <DiagramArrow label="Python strip().lower()" />
              <DiagramNode title="Фрагмент для сравнения"><strong>чай 50%</strong></DiagramNode>
            </DiagramFlow>
          </DiagramGroup>
        </Diagram>
        <p>
          Представим, что мы обработали «ЧАЙ V2» встроенной функцией SQLite <code>lower()</code>.
          Она меняет регистр латинских букв ASCII, и выходит «ЧАЙ v2». В этой строке фрагмент «чай» не
          встретится буквально. Python даёт «чай v2», поэтому прежние пути записи готовят копию именно
          его способом. При переименовании PUT и PATCH обновляют и исходное название, и поисковую копию.
        </p>
      </Section>

      <Section number="02" title="Буквальный фрагмент и значение запроса">
        <p>
          <code>instr(строка, фрагмент)</code> возвращает позицию первого вхождения, считая с 1.
          Например, для «чай 50%» и «%» получаем 7. Для «чай 500» и «%» получаем 0.
          Пустой фрагмент даёт 1, поэтому после <code>strip()</code> пустую строку сразу обрабатываем отдельно.
        </p>
        <p>
          <code>LIKE</code> проверяет шаблон. В шаблоне <code>%</code> разрешает любое число символов,
          а <code>_</code> разрешает один символ. Если обрамить введённое «50%» процентами, шаблон
          <code>%50%%</code> охватит и «50%», и «500». Для буквального поиска SQLite должна проверить
          каждый знак фрагмента как часть текста.
        </p>
        <h3>Опыт с витриной</h3>
        <p>
          В готовом файле SQLite открывается в памяти и получает четыре учебных товара. Достаточно
          сохранить весь блок в отдельный файл <code>catalog_search_demo.py</code> рядом с папкой
          <code>app</code> и выполнить <code>python catalog_search_demo.py</code> из той же папки,
          используя установленный для Planner Python. Можно сначала прочитать результат прямо здесь.
        </p>
        <p>
          Смотрим на <code>func.instr</code>. Первый аргумент указывает колонку
          <code>catalog.c.searchable_label</code>, второй содержит подготовленную строку Python.
          <code>func</code> даёт SQLAlchemy форму вызова SQL-функции. SQLAlchemy передаёт строку
          драйверу как отдельное значение, или связанный параметр. Даже кавычка тогда доходит до
          SQLite как обычный знак. <code>where</code> оставляет строки с положительной позицией,
          а <code>order_by</code> задаёт порядок id.
        </p>
        <CodeBlock language="python" caption="catalog_search_demo.py" code={catalogExample} />
        <p>
          В выводе строка перед списком показывает исходный ввод. В каждой паре первое число означает id,
          второе показывает позицию. Для одинарной кавычки программа дополнительно печатает SQL с местами
          для параметров и отдельный словарь значений. В SQL остаются <code>?</code>, среди значений видна
          сама кавычка. Ниже показана часть вывода с результатами поиска.
        </p>
        <CodeBlock language="text" caption="Результаты опыта" code={catalogResults} />
        <CatalogSearchProbe />
        <p>Попробуйте <code>_</code>, одинарную кавычку или запрос «  ЧаЙ  ». Затем проверьте строку из одних пробелов.</p>
      </Section>

      <Section number="03" title="От витрины к репозиторию Planner">
        <p>
          В Planner обработчик <code>handle_search</code> зовёт <code>search_tasks</code>, а сервис
          передаёт фрагмент в <code>repository.search</code>. Точку для нового метода ищем в
          <code>app/DBase/repositories.py</code> рядом с существующей SQL-выборкой. Витрина выводила
          пары id и позиции. Здесь результатом будут задачи со всеми предметными полями.
        </p>
        <p>
          Откройте знакомый <code>select</code> в репозитории. В нём условие и порядок собираются перед вызовом
          <code>self.session<wbr />.scalars(statement)<wbr />.all()</code>. Этот вызов получает выбранные ORM-строки.
          <code>task_from_row</code> преобразует их в привычные <code>Task</code>, сохраняя исходный
          <code>title</code> и <code>tags</code>. В новом методе вам предстоит выбрать условие и соединить его
          с этим способом чтения.
        </p>
        <p>
          Готовая проверка из практики показывает отдельно запрос и параметры. Чтобы увидеть, где
          сработал отбор, найдите <code>WHERE</code> в SQL и обработанный фрагмент среди параметров.
          Полное чтение таблицы тоже может привести к верному списку после обработки в Python, поэтому
          одних id для проверки недостаточно. Для пустого ввода посмотрите прямой вызов репозитория:
          ему соответствует 0 запросов. У CLI собственное сообщение для пустого ввода, которое появляется
          ещё до обращения к репозиторию.
        </p>
        <QuizCard
          question="В витрине товар «Чай_подарок» переименовали в «Чай коллекция». Что покажет поиск, если обновились исходное название и поисковая копия?"
          options={[
            "Подчёркивание всё ещё находит id 3, «коллекция» даёт пустой список.",
            "Подчёркивание даёт пустой список, «коллекция» находит id 3.",
            "Оба фрагмента находят id 3.",
          ]}
          correctIndex={1}
          explanation="После записи поисковая копия товара равна «чай коллекция». В ней больше нет подчёркивания, зато появился новый фрагмент. Именно поэтому проверка перечитывает задачу после записи."
        />
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>Работаем в своём Planner, где SQL-репозиторий уже умеет выбирать и обновлять задачи. В условии лежит готовый проверочный файл.</Lead>
        <LinkedNotes items={[
          { title: "Найдём фрагмент буквально", description: "Напишем один метод SQL-репозитория и получим совпавшие id в порядке возрастания." },
          { title: "Проверим знакомый поиск", description: "Увидим строки CLI и отдельно проверим SQL и его параметры. Пустой фрагмент даст пустой список при нуле запросов." },
          { title: "Сравним результат после записи", description: "После готового переименования старый фрагмент исчезнет из выдачи, а новый найдёт задачу с тем же id и тегами." },
        ]} />
        <p>Список совпадений показывает поведение для пользователя. Запрос показывает, где был сделан отбор, а повторное чтение проверяет связь поиска с новым названием.</p>
      </Section>
    </RichLesson>
  );
}
