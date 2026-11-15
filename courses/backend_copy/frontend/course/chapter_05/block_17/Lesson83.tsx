import { useState } from "react";
import { CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section, TypeCard, TypeCards } from "../../shared";

const envExample = `from pathlib import Path
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from models import Base

config = context.config
target_metadata = Base.metadata


def checked_url():
    raw = config.get_main_option("sqlalchemy.url")
    expected = config.get_main_option("expected_database_path")
    if not raw or not expected:
        raise ValueError("Нужны URL и ожидаемый путь")
    url = make_url(raw)
    if (url.drivername not in ("sqlite", "sqlite+pysqlite")
            or url.username is not None or url.password is not None
            or url.host is not None or url.port is not None
            or url.query or not url.database
            or url.database == ":memory:"):
        raise ValueError("Нужен локальный файл SQLite")
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise ValueError("Ожидаемый путь должен быть абсолютным")
    actual_path = Path(url.database).resolve()
    expected_path = expected_path.resolve()
    print("URL:", actual_path)
    print("Ожидаем:", expected_path)
    if actual_path != expected_path:
        raise ValueError("Пути не совпадают")
    return raw


def run_migrations_offline():
    url = checked_url()
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    checked_url()
    engine = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()`;

const modelsExample = `from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)`;

const configExample = `from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy.engine import URL

target = Path("sandbox/check.sqlite3").resolve()
url = URL.create("sqlite", database=target.as_posix())
cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
cfg.set_main_option("expected_database_path", str(target))
command.current(cfg)`;

function HistoryExplorer() {
  const [state, setState] = useState<"empty" | "first" | "latest">("empty");
  const descriptions = {
    empty: "Файлы: ревизий нет, head отсутствует. Новая база: current отсутствует.",
    first: "Файлы: a → b → c, head равен c. Первая база: current равен a.",
    latest: "Файлы: a → b → c, head равен c. Вторая база: current равен c.",
  };
  function select(next: "empty" | "first" | "latest") {
    setState(next);
  }
  return <div className="quiz-card" style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
    <div className="quiz-question"><strong>Сравним файлы и выбранную базу</strong></div>
    <p>Переключим пример и увидим head файлов и current выбранной базы. Это демонстрация: переключение базы не меняет историю.</p>
    <div className="branch-scenarios answer-options">
      <button className={`branch-chip answer-option ${state === "empty" ? "is-active" : ""}`} type="button" aria-pressed={state === "empty"} onClick={() => select("empty")}>Нет ревизий</button>
      <button className={`branch-chip answer-option ${state === "first" ? "is-active" : ""}`} type="button" aria-pressed={state === "first"} onClick={() => select("first")}>Первая база</button>
      <button className={`branch-chip answer-option ${state === "latest" ? "is-active" : ""}`} type="button" aria-pressed={state === "latest"} onClick={() => select("latest")}>Вторая база</button>
      <button className="branch-chip answer-option" type="button" onClick={() => select("empty")}>Сбросить</button>
    </div>
    <p role="status">{descriptions[state]}</p>
  </div>;
}

function ObservationExplorer() {
  const [caseIndex, setCaseIndex] = useState(0);
  const [answer, setAnswer] = useState<number | null>(null);
  const cases = [
    { label: "История", data: "Каталог versions/ пуст. Что покажет heads?", correct: 0, reason: "Без файлов ревизий в этой истории нет вершины head." },
    { label: "Новая база", data: "Файлы a → b существуют, а таблицы отметок в новой базе нет. Что покажет current?", correct: 1, reason: "Файлы истории существуют, но выбранная база ещё не получила отметку." },
    { label: "После stamp", data: "Гипотетически stamp записал current = head = b, но переход к b добавлял столбец note. Что можно заключить об этом столбце?", correct: 2, reason: "Stamp изменил отметку без перехода. Совпадение current и head не добавило столбец и не доказало схему." },
  ];
  const options = ["Вершины head нет", "Текущая отметка отсутствует", "Нужно отдельно проверить схему"];
  const selected = cases[caseIndex];
  function chooseCase(index: number) {
    setCaseIndex(index);
    setAnswer(null);
  }
  return <div className="quiz-card" style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
    <div className="quiz-question"><strong>Какой вывод следует из наблюдения?</strong></div>
    <p>Выберем ситуацию, затем подходящий вывод. В случае stamp действие только представлено, на базе мы его не выполняем.</p>
    <div className="branch-scenarios answer-options">
      {cases.map((item, index) => <button key={item.label} className={`branch-chip answer-option ${caseIndex === index ? "is-active" : ""}`} type="button" aria-pressed={caseIndex === index} onClick={() => chooseCase(index)}>{item.label}</button>)}
      <button className="branch-chip answer-option" type="button" onClick={() => chooseCase(0)}>Сбросить</button>
    </div>
    <p><strong>Наблюдение:</strong> {selected.data}</p>
    <div className="quiz-options answer-options">
      {options.map((option, index) => <button key={option} className={`quiz-option answer-option ${answer === index ? (index === selected.correct ? "is-correct" : "is-wrong") : ""}`} type="button" aria-pressed={answer === index} onClick={() => setAnswer(index)}>{option}</button>)}
    </div>
    {answer !== null && <p className={`quiz-explanation ${answer === selected.correct ? "is-correct" : "is-wrong"}`} role="status">{answer === selected.correct ? "Верно. " : "Проверим ещё раз. "}{selected.reason}</p>}
  </div>;
}

export function Lesson83({ module }: { module?: string }) {
  return <RichLesson>
    <RichHero chip={module ?? "Изменение заполненной схемы"} title="История схемы и Alembic" intro="Подготовим отдельное место для истории изменений и проверим адрес базы до подключения." />
    <Section number="00" title="От восстановленной копии к истории">
      <Lead>Проверенная копия помогает вернуть данные. Но по одному файлу не видно, какие изменения схемы и в каком порядке были задуманы.</Lead>
      <LinkedNotes items={[
        { title: "Точка старта", description: "У нас есть учебная опись и восстановленная копия, если её содержимое проверено." },
        { title: "Новый вопрос", description: "Как отделить историю изменений от текущей структуры и адреса конкретной базы?" },
        { title: "Результат", description: "Настроим Alembic на отдельный учебный файл и изучим пустую историю." },
      ]} />
      <p>Снимок отвечает за восстановление содержимого. История описывает переходы схемы. Перед запуском инструмента нам также нужно знать, к какому файлу он обратится.</p>
    </Section>
    <Section number="01" title="Схема, данные и история">
      <Lead>Разделим три вопроса, которые часто скрываются за словом «база».</Lead>
      <p>Схема описывает таблицы, столбцы и ограничения. Данные это строки таблиц. История хранит описания переходов от одной схемы к другой. Допустим, в библиотеке таблица <code>books</code> содержит столбцы <code>id</code> и <code>title</code> и две книги. Позже к таблице добавили необязательное поле заметки. Структура изменилась, но названия двух книг могут остаться прежними.</p>
      <p>Снимок может сохранить обе книги и схему на момент копирования, но он не объясняет порядок изменений. Команда <code>create_all</code> создаёт отсутствующие таблицы, однако не добавляет столбцы в уже существующую. Поэтому одинаковое имя таблицы ещё не доказывает совпадение её структуры и истории.</p>
      <p>Подумаем: если у каталога товаров изменилось ограничение цены, а строки те же, что изменилось? Именно схема. О наличии записи в истории по этому наблюдению судить нельзя.</p>
    </Section>
    <Section number="02" title="Ревизии и состояние базы">
      <Lead>История состоит из файлов переходов, но отметка о выполнении принадлежит конкретной базе.</Lead>
      <p>Ревизия, или <code>revision</code>, это файл с собственным идентификатором и описанием изменений. Поле <code>down_revision</code> ссылается на родителя. У первой ревизии родителя нет. В линейной истории <code>a → b → c</code> последняя доступная ревизия файлов называется <code>head</code>, здесь это <code>c</code>.</p>
      <p><code>current</code> показывает отметку конкретной базы, обычно из таблицы <code>alembic_version</code>. Если первая база отмечена <code>a</code>, а вторая <code>c</code>, head файлов для обеих всё равно один: <code>c</code>. При отсутствии файлов ревизий head отсутствует; у новой базы без отметки нет current.</p>
      <Diagram caption="Файлы описывают порядок переходов; отметка находится отдельно в выбранной базе" orientation="vertical">
        <DiagramFlow orientation="vertical" label="Линейная история файлов">
          <DiagramNode title="Ревизия a"><p>У первой ревизии нет родителя.</p></DiagramNode>
          <DiagramArrow label="переход к b; родитель b это a" />
          <DiagramNode title="Ревизия b"><p>Продолжает историю после a.</p></DiagramNode>
          <DiagramArrow label="переход к c; родитель c это b" />
          <DiagramNode title="Ревизия c: head"><p>Последняя доступная ревизия файлов.</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
      <p>Стрелки показывают порядок истории. Отметка каждой базы хранится отдельно и не передвигает эти файлы: первая база может оставаться на <code>a</code>, пока вторая уже отмечена <code>c</code>.</p>
      <HistoryExplorer />
      <p>Отметка не доказывает, что все столбцы действительно соответствуют ревизии. Для этого нужно отдельно прочитать фактическую схему.</p>
      <p>Попробуем на другой истории: в файлах есть <code>x → y</code>, а выбранная база отмечена <code>x</code>. Кто родитель <code>y</code>, чему равны head и current? Сверим: родитель это <code>x</code>, head файлов это <code>y</code>, current базы это <code>x</code>. Добавление второго файла не передвигает отметку уже существующей базы.</p>
    </Section>
    <Section number="03" title="Применение и маркировка">
      <Lead>Похожие команды отвечают на разные вопросы.</Lead>
      <TypeCards>
        <TypeCard badge="upgrade" title="Применяем переходы">Выполняет нужные переходы существующей истории к выбранной цели и обновляет отметку.</TypeCard>
        <TypeCard badge="stamp" title="Записываем отметку">Записывает отметку ревизии без выполнения переходов и без исправления схемы.</TypeCard>
      </TypeCards>
      <p>Пусть ревизия <code>b</code> добавляет в библиотечную таблицу столбец <code>note</code>. После успешного применения перехода столбец появляется. Если же только поставить отметку <code>b</code>, столбец сам не появится. Равенство current и head после такой отметки не доказывает соответствие схемы. Пока мы исследуем эти действия на схеме и не применяем их к заполненной базе.</p>
      <p>Перенесём рассуждение на каталог: база отмечена первой ревизией, а следующая описывает добавление столбца цены. Что произойдёт при применении перехода и при одной маркировке? В первом случае выполнится описанное добавление, во втором изменится только отметка. Чтобы узнать фактическую структуру, нужно отдельно её прочитать.</p>
    </Section>
    <Section number="04" title="Один Python и зависимости">
      <Lead>Прежде чем настраивать инструмент, убедимся, что установка и запуск используют одну учебную среду.</Lead>
      <p>Работаем в PowerShell из папки <code>project/</code> изолированной учебной копии. Виртуальная среда <code>.venv</code> содержит отдельный Python и пакеты. Если среда существует, сначала проверяем её принадлежность и состав; неизвестную среду не изменяем.</p>
      <p>В независимом мини-проекте <code>library_demo/project/</code> файл <code>requirements.txt</code> содержит <code>SQLAlchemy==2.0.55</code> и <code>alembic==1.18.4</code>. Файл <code>requirements-dev.txt</code> содержит <code>-r requirements.txt</code>, <code>pytest==9.2.0</code> и <code>httpx2==2.13.1</code>. Первый список задаёт зависимости приложения и миграций, второй включает их и инструменты проверки.</p>
      <p>Создадим эти два текстовых файла именно в корне мини-проекта: каждая указанная зависимость занимает отдельную строку. При установке dev-файла pip сначала прочитает и основной файл через <code>-r requirements.txt</code>. Знак <code>==</code> закрепляет выбранную версию: например, <code>alembic==1.18.4</code> не означает любую последнюю версию.</p>
      <CodeBlock caption="library_demo/project/requirements.txt" code={`SQLAlchemy==2.0.55
alembic==1.18.4`} />
      <CodeBlock caption="library_demo/project/requirements-dev.txt" code={`-r requirements.txt
pytest==9.2.0
httpx2==2.13.1`} />
      <p>Сначала проверим, где мы находимся и существует ли папка для опыта. В новом учебном мини-проекте <code>Get-Location</code> показывает текущий каталог, а <code>Test-Path -LiteralPath</code> проверяет точное имя без поиска по шаблону. Если папки нет, <code>New-Item -ItemType Directory</code> создаёт каталог, но не базу.</p>
      <CodeBlock caption="Отдельная папка для примера" code={`Get-Location
Test-Path -LiteralPath .\\sandbox
# Если результат False, создаём отдельный каталог:
New-Item -ItemType Directory -Path .\\sandbox
Test-Path -LiteralPath .\\sandbox`} />
      <p>После создания последняя проверка возвращает <code>True</code>. Если каталог уже существует, не создаём его повторно и сначала выясняем, что в нём лежит. Для самостоятельной пробы выберем другое свободное имя в своём мини-проекте и проверим его до и после создания.</p>
      <p>В следующем примере <code>.venv</code> ещё нет. Если она уже существует, не выполняем команду создания поверх неё: сначала выясняем, относится ли она к этой учебной копии.</p>
      <CodeBlock caption="Подготовка новой среды в отдельном примере" code={`py -3.12 --version
py -3.12 -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -c "import sys; print(sys.executable)"
python -m pip install -r requirements-dev.txt
python -m pip check
python -m alembic --version
python -m pytest --version`} />
      <p>Команда <code>-m</code> запускает модуль выбранным Python, <code>venv</code> создаёт среду, а <code>-r</code> просит pip прочитать список из файла. <code>sys.executable</code> показывает путь запущенного интерпретатора. После активации он должен находиться внутри учебной <code>.venv</code>; иначе остановимся до установки. Если запуск активации запрещён, используем <code>.\.venv\Scripts\python.exe</code> вместо <code>python</code> во всех командах и не меняем политику системы.</p>
      <p>Строка версии Python должна начинаться с <code>Python 3.12</code>, а путь интерпретатора содержать <code>library_demo/project/.venv</code>, с разделителями Windows. Успешный <code>pip check</code> сообщает <code>No broken requirements found.</code>; команды версий выводят номер Alembic и pytest. Если видим другой путь или ошибку зависимости, не продолжаем: сначала установим, какая среда активна.</p>
      <p>Если <code>pip check</code> сообщает о конфликте или пакета нет, сначала выясняем причину в учебной среде. Само имя пакета не подтверждает, что история миграций уже существует.</p>
    </Section>
    <Section number="05" title="Конфигурация и проверка адреса">
      <Lead>SQLite может создать отсутствующий файл при подключении. Поэтому адрес проверяем до создания соединения.</Lead>
      <p>Продолжим независимый пример из <code>library_demo/project/</code>. Создадим папку <code>sandbox</code> и проверим, что <code>alembic.ini</code> и <code>migrations</code> ещё отсутствуют. Команда <code>python -m alembic init migrations</code> создаст ini и каталог окружения. В нём <code>env.py</code> управляет подключением, а пустой <code>versions/</code> предназначен для будущих файлов ревизий. Повторно поверх существующих файлов init не запускаем.</p>
      <p>Команду <code>init</code> выполняем из <code>library_demo/project/</code>, а не из <code>sandbox/</code>. После неё в корне появляется <code>alembic.ini</code>, а в <code>migrations/</code> лежат <code>env.py</code> и каталог <code>versions/</code>. В созданном ini значение <code>script_location</code> направляет Alembic к этому каталогу миграций. Пока файл ревизии не создан, перечислять в истории нечего.</p>
      <p>В разделе <code>[alembic]</code> зададим один URL соединения <code style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>sqlalchemy.url = sqlite:///./sandbox/library_a.sqlite3</code>. Три косые черты задают относительный путь от текущего каталога. Второй параметр <code>expected_database_path</code> содержит абсолютный ожидаемый путь к тому же учебному файлу. Это контрольное значение, а не второй URL.</p>
      <CodeBlock caption="Получаем абсолютный путь до подключения" code={`python -c "from pathlib import Path; print(Path('sandbox/library_a.sqlite3').resolve())"`} />
      <p>Скопируем выведенный абсолютный путь в <code>expected_database_path</code>. <code>Path</code> представляет путь, а <code>resolve()</code> приводит его к абсолютному виду. Сначала по описи убеждаемся, что цель находится в отдельном учебном каталоге и не совпадает с оригиналом или снимком. Совпадение двух настроек само по себе не доказывает, что выбор безопасен.</p>
      <p>Для примера сохраним следующий полный файл <code>models.py</code> в корне мини-проекта. Он описывает книгу, но не создаёт таблицу в SQLite.</p>
      <CodeBlock caption="library_demo/project/models.py" code={modelsExample} />
      <p><code>Base.metadata</code> хранит описание таблиц. Из корня мини-проекта выполним <code style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>python -c "from models import Base; print(list(Base.metadata.tables))"</code>. Увидим <code>['books']</code>: импорт загрузил класс и зарегистрировал его описание в metadata. Физическую таблицу мы пока не создавали, а сама проверка не остаётся в файле модели.</p>
      <p>Прежде чем читать конфигурацию Alembic, отдельно проверим, как извлечь путь из адреса и сравнить его с ожидаемым. В корне <code>library_demo/project/</code> сохраним следующий файл <code>check_url.py</code> и запустим <code>python check_url.py</code>. Этот опыт не открывает и не создаёт файл базы.</p>
      <CodeBlock caption="library_demo/project/check_url.py" code={`from pathlib import Path
from sqlalchemy.engine import make_url

url = make_url("sqlite:///./sandbox/library_a.sqlite3")
actual = Path(url.database).resolve()
expected = Path("sandbox/library_a.sqlite3").resolve()
other = Path("sandbox/library_b.sqlite3").resolve()
print("Драйвер:", url.drivername)
print("Путь из URL:", url.database)
print("Ожидаемая цель:", actual == expected)
print("Другая цель:", actual == other)`} />
      <p>Результат по строкам: <code>Драйвер: sqlite</code>, <code>Путь из URL: ./sandbox/library_a.sqlite3</code>, <code>Ожидаемая цель: True</code>, <code>Другая цель: False</code>. <code>make_url</code> разбирает строку, <code>url.database</code> даёт путь, а <code>resolve()</code> приводит оба пути к абсолютной форме. Первая пара указывает на один файл; вторая на разные. Сравнение само по себе не решает, разрешено ли использовать выбранный файл.</p>
      <p>Теперь самостоятельно изменим только ожидаемое имя первой пары на <code>library_b.sqlite3</code> и повторим запуск. Обе проверки равенства покажут <code>False</code>: адрес по-прежнему выбирает <code>library_a.sqlite3</code>. Вернём первоначальное имя перед следующим опытом.</p>
      <p>Перед большим файлом разделим задачу на три вопроса. Сначала <code>get_main_option</code> читает выбранный адрес и ожидаемый путь. Затем <code>make_url</code> выделяет путь из адреса, а <code>Path.resolve()</code> приводит его к абсолютному виду. Наконец, сравнение решает, можно ли продолжить. Пока не дошли до последнего шага, соединение не создаётся.</p>
      <p>Посмотрим части будущего <code>library_demo/project/migrations/env.py</code> до целого файла. Это фрагменты для чтения, отдельно их запускать не нужно. В начале <code>context.config</code> передаёт настройки из выбранного <code>alembic.ini</code>; тот же объект может быть настроен программно. Оба значения читаются из него, а описание модели берётся из <code>models.py</code>:</p>
      <CodeBlock caption="Фрагмент env.py: настройки и описание модели" code={`config = context.config
target_metadata = Base.metadata

def checked_url():
    raw = config.get_main_option("sqlalchemy.url")
    expected = config.get_main_option("expected_database_path")`} />
      <p>На этом шаге <code>raw</code> содержит адрес соединения, <code>expected</code> контрольный абсолютный путь. Отсутствие любого из них прекращает работу, поскольку сравнивать пока нечего. Перед продолжением полный файл также проверяет тип URL: сетевой адрес, параметры и базу в памяти этот учебный сценарий не принимает.</p>
      <CodeBlock caption="Фрагмент checked_url: сравниваем до соединения" code={`actual_path = Path(url.database).resolve()
expected_path = expected_path.resolve()
print("URL:", actual_path)
print("Ожидаем:", expected_path)
if actual_path != expected_path:
    raise ValueError("Пути не совпадают")
return raw`} />
      <p>Здесь <code>url</code> уже получен через <code>make_url(raw)</code>, а абсолютность ожидаемого пути проверена ранее в той же функции. Одинаковые строки показывают, что оба параметра указывают на одну цель; разные вызывают ошибку. Это не открывает базу: обращение произойдёт только после возврата URL.</p>
      <p>Последняя часть выбирает способ работы. В offline-ветви проверка идёт до <code>context.configure</code>, в online-ветви до создания engine и подключения. Поэтому неверный адрес останавливает обе ветви. После успешной online-проверки соединение передаётся Alembic и закрывается при выходе из <code>with</code>; пустая история при этом не создаёт таблицу книги.</p>
      <p>Например, если URL указывает на <code>library_a.sqlite3</code>, а ожидаемый путь заканчивается на <code>library_b.sqlite3</code>, два разрешённых пути не равны. Функция сообщает «Пути не совпадают» и останавливает команду. При совпадении она возвращает исходный URL для дальнейшей настройки. Это проверка согласованности двух параметров, а безопасное расположение файла мы выбираем отдельно.</p>
      <p>В полном файле сначала объявлена функция <code>checked_url()</code>: сама её запись ещё не открывает базу. Затем описаны два способа работы Alembic. Online получает настоящее соединение; offline готовит SQL без него. Оба способа вызывают проверку прежде, чем передать адрес или соединение в настройку. В конце выбор режима запускает соответствующую функцию.</p>
      <p>Заменим содержимое <code>migrations/env.py</code> в мини-проекте следующим примером. Импорты берут конфигурацию Alembic, SQLAlchemy и модель книги. Функция читает два значения из одного Config, разбирает URL и сравнивает пути до создания engine. В offline-режиме та же проверка выполняется до подготовки SQL.</p>
      <CodeBlock caption="library_demo/project/migrations/env.py" code={envExample} />
      <p><code>make_url</code> разбирает строку, не подключаясь. <code>drivername</code> и <code>database</code> показывают драйвер и путь. Мы допускаем только обычный локальный SQLite-файл без сетевого адреса, параметров и <code>:memory:</code>. Ожидаемый путь должен быть абсолютным. <code>raise ValueError</code> завершает команду, если данные непонятны или пути различаются. Эта учебная проверка не защищает от подмены файлов другими процессами.</p>
      <p><code>context.config</code> содержит выбранную конфигурацию, а <code>get_main_option</code> читает значение по имени. После успешной проверки <code>get_section</code> передаёт настройки в <code>engine_from_config</code>; префикс <code>sqlalchemy.</code> указывает, какие настройки относятся к engine. <code>NullPool</code> не удерживает соединение в пуле. В блоке <code>with engine.connect()</code> получаем и затем закрываем соединение, а <code>engine.dispose()</code> освобождает ресурсы engine. <code>context.configure</code> связывает Alembic с соединением или URL и metadata, <code>begin_transaction</code> задаёт границу операции, <code>run_migrations</code> передаёт управление выбранной истории. При пустой истории это не создаёт таблицу книги.</p>
      <p>Запустим <code>python -m alembic current</code>. Два выведенных абсолютных пути совпадают; при пустой истории текущей ревизии нет. Теперь выберем другой свободный файл <code>sandbox/library_b.sqlite3</code>: изменим в ini и URL, и ожидаемый абсолютный путь, снова проверим равенство и повторим current. Если изменить только URL на свободный <code>sandbox/wrong.sqlite3</code>, проверка остановится с ошибкой до подключения, а файл wrong не появится. Вернём согласованную пару для второго файла.</p>
      <p>Для тестовой конфигурации можно передать оба значения в объект <code>Config</code>. В том же мини-проекте сохраним <code>show_current.py</code> и запустим <code>python show_current.py</code>:</p>
      <CodeBlock caption="library_demo/project/show_current.py" code={configExample} />
      <p><code>Config("alembic.ini")</code> читает настройки той же истории из ini. <code>URL.create</code> собирает адрес: первый аргумент выбирает SQLite, а <code>database</code> получает абсолютный путь в форме с косыми чертами. <code>render_as_string</code> возвращает строку адреса, а каждый вызов <code>set_main_option</code> меняет одну настройку этого Config. <code>env.py</code> получает тот же объект через <code>context.config</code>. В выводе пути совпадают, current пуст. Если для следующей пробы изменить только URL, сохранив прежний ожидаемый путь, вызов завершится отказом до создания ошибочной базы. При буквальном знаке <code>%</code> в значении ConfigParser требует записать <code>%%</code>; в нашем учебном каталоге выберем путь без него.</p>
      <p>Продолжим тот же <code>show_current.py</code>: после первого вызова выберем ещё не существующее имя и изменим только URL. <code>try</code> перехватывает ожидаемый <code>ValueError</code>, чтобы мы могли проверить путь после отказа.</p>
      <CodeBlock caption="Продолжение show_current.py" code={`wrong = Path("sandbox/wrong_config.sqlite3").resolve()
wrong_url = URL.create("sqlite", database=wrong.as_posix())
cfg.set_main_option("sqlalchemy.url", wrong_url.render_as_string(hide_password=False))
try:
    command.current(cfg)
except ValueError as error:
    print(error)
print(wrong.exists())`} />
      <p>В конце вывода увидим сообщение <code>Пути не совпадают</code> и <code>False</code>: неверный файл не появился. Если файл уже существует до пробы, выбираем другое свободное имя, а не удаляем существующий файл.</p>
      <p>Для самостоятельной пробы выберем ещё две свободные цели в отдельной учебной папке. Для каждой зададим в ini согласованную пару и посмотрим current; затем заменим только URL на третью свободную цель и убедимся, что она не создаётся. В отдельном Config так же изменим обе настройки для своего файла, а затем только URL для неверного случая. На каждом шаге сначала сверяем пути с описью.</p>
    </Section>
    <Section number="06" title="Модель и настоящая таблица">
      <Lead>Инструменту пригодится описание модели, но импорт модели не выполняет миграцию.</Lead>
      <p>В примере строка <code>from models import Base</code> загружает классы книги, а <code>target_metadata = Base.metadata</code> передаёт их описание Alembic для будущего сравнения. В исходном шаблоне вместо этого стоит <code>target_metadata = None</code>: такая настройка ещё не передаёт описание модели. Файлы модели и ревизии имеют разные роли: metadata может измениться, поэтому она не заменяет неизменяемую историю.</p>
      <p>В Planner описание находится в <code>app/DBase/models.py</code>. В будущей практике мы сами подключим его <code>Base</code>. После этого наличие имени <code>tasks</code> в metadata ещё не означает, что таблица появилась в выбранном файле.</p>
      <p>Для самостоятельной пробы можно в отдельном мини-проекте описать вместо книги таблицу <code>supplies</code>, импортировать её Base в окружение и вывести имена metadata. Имя <code>supplies</code> в списке показывает, что описание загружено; оно само по себе не показывает таблицу в SQLite. Если импорт завершился ошибкой, сначала сверим имя модуля и рабочий каталог.</p>
    </Section>
    <Section number="07" title="Как увидеть пустую историю">
      <Lead>Проверим файлы истории, затем отметку выбранной базы и отдельно её таблицы.</Lead>
      <CodeBlock caption="Команды из project/ после проверки обоих путей" code={`python -m alembic history
python -m alembic heads
python -m alembic current`} />
      <p><code>history</code> перечисляет файлы ревизий, <code>heads</code> показывает вершины их истории, а <code>current</code> обращается к выбранной базе. В пустом каталоге нет ревизий и head; в новой базе нет current. Точный служебный вывод может отличаться. Эти команды не создают таблицу <code>books</code> из одной только metadata.</p>
      <p>Разберём наблюдение: отсутствие строк ревизий после <code>history</code> означает, что в текущей истории нечего перечислять; отсутствие head после <code>heads</code> означает, что у неё нет вершины. <code>current</code> может напечатать служебные сообщения и проверенные пути, но не идентификатор ревизии: выбранная новая база ещё не отмечена. Отсутствие идентификатора не служит проверкой таблиц, поэтому следующий шаг читает их отдельно.</p>
      <ObservationExplorer />
      <p>Чтобы увидеть реальные таблицы, сначала убедимся, что выбранный учебный файл существует. Если <code>current</code> ещё не создал его, повторно проверим разрешённый путь и отсутствие файла, затем создадим только новую цель: <code style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>python -c "import sqlite3; sqlite3.connect('sandbox/library_b.sqlite3').close()"</code>. Подключение создаёт файл, <code>close()</code> закрывает его; таблиц приложение при этом не создаёт.</p>
      <p>Теперь откроем существующий учебный файл только для чтения. В Python параметр <code>uri=True</code> позволяет использовать <code>mode=ro</code>: ошибка в пути не создаст новый файл. Запрос к <code>sqlite_schema</code> перечисляет объекты SQLite; служебную таблицу Alembic нельзя считать таблицей приложения. Знак <code>?</code> в запросе обозначает параметр, а кортеж <code>('table',)</code> передаёт значение отдельно от SQL. Запятая отличает кортеж из одного элемента от строки в скобках.</p>
      <CodeBlock caption="Проверяем пример в project/" code={`python -c "import sqlite3; c=sqlite3.connect('file:sandbox/library_b.sqlite3?mode=ro', uri=True); print(c.execute('SELECT name FROM sqlite_schema WHERE type=?', ('table',)).fetchall()); c.close()"`} />
      <p>Пустой список <code>[]</code> означает, что запрос не нашёл таблиц. Может встретиться служебная таблица Alembic: её наличие не означает появления <code>books</code>. Ошибка открытия отличается от пустого списка: она сообщает, что проверить выбранный файл не удалось. Список без <code>books</code> подтверждает только состояние этого файла.</p>
      <p>Для исходной базы, снимка и восстановленной копии остановим записывающие процессы и снимем контрольные значения командой <code>Get-FileHash -Algorithm SHA256 -LiteralPath &lt;путь&gt;</code>. До и после опыта сравниваем каждый файл с его собственным значением. Хеш показывает изменение файла, но не заменяет содержательную сверку восстановления.</p>
      <p>В отдельной папке <code>sandbox</code> сначала проверим, что <code>note.txt</code> отсутствует. Следующие команды создают короткий учебный файл и дважды получают его SHA256. Метод <code>write_text</code> записывает строку в названный файл, поэтому существующий файл для этого примера не используем.</p>
      <CodeBlock caption="Контроль одного учебного файла" code={`Test-Path -LiteralPath .\\sandbox\\note.txt
python -c "from pathlib import Path; Path('sandbox/note.txt').write_text('abc', encoding='utf-8')"
Get-FileHash -Algorithm SHA256 -LiteralPath .\\sandbox\\note.txt
Get-FileHash -Algorithm SHA256 -LiteralPath .\\sandbox\\note.txt`} />
      <p>Первая команда должна показать <code>False</code>. Два значения хеша совпадут, потому что между вызовами мы не меняли файл. На другом новом файле самостоятельно повторим сравнение и затем изменим текст: новое значение будет отличаться.</p>
    </Section>
    <Section number="08" title="Что мы будем делать в практике">
      <Lead>Настроим историю в учебной копии Planner и проверим её без изменения заполненной базы.</Lead>
      <p>Будем двигаться от безопасной среды к наблюдению за новой базой. Результат каждой карточки понадобится в следующей, поэтому проверим его до перехода.</p>
      <LinkedNotes items={[
        { title: "Учебная среда", description: "Проверим Python в отдельной .venv, добавим закреплённый Alembic и убедимся, что зависимости установлены без конфликта." },
        { title: "Отдельная цель", description: "Найдём исходную базу, снимок и восстановленную копию; запишем их хеши и выберем ещё не существующий четвёртый путь." },
        { title: "Настройка истории", description: "Создадим окружение Alembic, подключим metadata Planner и проверим, что несовпадающий URL останавливает подключение." },
        { title: "Пустое состояние", description: "Прочитаем историю, отметку и таблицы новой базы, затем сравним каждый сохранённый хеш с его исходным значением." },
      ]} />
      <p>Результатом станет подготовленное окружение с пустой историей, а не готовая миграция. Если адрес неизвестен или восстановление не проверено, сначала вернёмся к этим опорам и не подключимся к случайному файлу.</p>
    </Section>
  </RichLesson>;
}
