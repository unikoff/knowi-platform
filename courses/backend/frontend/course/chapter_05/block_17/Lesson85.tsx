import { RichLesson, RichHero, Section, Lead, LinkedNotes, CodeBlock, Callout,
  Diagram, DiagramLane, DiagramFlow, DiagramNode, DiagramArrow, DiagramNote,
  CodeSequence, QuizCard } from "@course-ui";

const labelProbe = String.raw`from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from sqlalchemy import CheckConstraint, Integer, String, create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase):
    pass

class LabelRow(Base):
    __tablename__ = 'labels'
    __table_args__ = (CheckConstraint('weight IN (1, 2)'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    note: Mapped[str] = mapped_column(String)
    weight: Mapped[int] = mapped_column(Integer)

def label(code, note='internal fixture', weight=1):
    return LabelRow(code=code, note=note, weight=weight)

def is_code_conflict(error):
    original = error.orig
    return (
        getattr(original, 'sqlite_errorcode', None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(original) == 'UNIQUE constraint failed: labels.code'
    )

with TemporaryDirectory(prefix='labels-probe-', dir=Path.cwd()) as temporary:
    path = Path(temporary) / 'labels.sqlite3'
    engine = create_engine(URL.create('sqlite', database=path.as_posix()), echo=False)
    Factory = sessionmaker(bind=engine)
    try:
        # This new temporary database belongs only to this example.
        Base.metadata.create_all(engine)
        with Factory() as setup:
            setup.add(label('kept'))
            setup.commit()
        if not path.is_file():
            raise FileNotFoundError(path)
        with Factory() as reading:
            row = reading.scalars(select(LabelRow).where(LabelRow.code == 'kept')).one_or_none()
            kept_id = row.id
            public = {'id': row.id, 'code': row.code}
            print('found:', public['code'])
            print('by id:', reading.get(LabelRow, kept_id).code)
            missing = reading.scalars(select(LabelRow).where(LabelRow.code == 'missing')).one_or_none()
            print('missing:', missing)
            reading.rollback()
        with Factory() as cancelled:
            candidate = label('cancelled')
            cancelled.add(candidate)
            cancelled.flush()
            cancelled_id = candidate.id
            cancelled.rollback()
        with Factory() as verification:
            print('cancelled absent:', verification.get(LabelRow, cancelled_id) is None)
        # Two independent connections to one existing file, not two memory databases.
        with engine.connect() as connection_a, engine.connect() as connection_b:
            with Factory(bind=connection_a) as read_a:
                first = read_a.scalars(select(LabelRow).where(LabelRow.code == 'draft')).one_or_none()
                read_a.rollback()
            with Factory(bind=connection_b) as read_b:
                second = read_b.scalars(select(LabelRow).where(LabelRow.code == 'draft')).one_or_none()
                read_b.rollback()
            print('two checks:', first, second)
            with Factory(bind=connection_a) as write_a:
                write_a.add(label('draft', note='first attempt'))
                write_a.commit()
            with Factory(bind=connection_b) as write_b:
                try:
                    write_b.add(label('draft', note='second attempt'))
                    write_b.flush()
                    write_b.commit()
                except IntegrityError as error:
                    confirmed = is_code_conflict(error)
                    # Diagnostic only. Application code rolls back immediately.
                    try:
                        write_b.get(LabelRow, kept_id)
                    except PendingRollbackError:
                        print('rollback required:', True)
                    write_b.rollback()
                    if not confirmed:
                        raise
                    print('code conflict:', confirmed)
                write_b.add(label('review', weight=2))
                write_b.commit()
        with Factory() as invalid:
            try:
                invalid.add(label('bad_weight', weight=7))
                invalid.flush()
            except IntegrityError as error:
                confirmed = is_code_conflict(error)
                invalid.rollback()
                print('CHECK is code conflict:', confirmed)
        with Factory() as verification:
            rows = verification.scalars(select(LabelRow).order_by(LabelRow.code)).all()
            print('saved:', [row.code for row in rows])
            draft = verification.scalars(select(LabelRow).where(LabelRow.code == 'draft')).one_or_none()
            print('first attempt kept:', draft.note == 'first attempt')
        print('public copy:', public['code'])
    finally:
        engine.dispose()
`;

export function Lesson85() {
  return <RichLesson>
    <RichHero chip="Миграции базы" title="Уникальный логин"
      intro="Проверим, кто действительно решает, свободно ли имя, и как продолжить работу после отклонённой записи." />
    <Section number="00" title="От таблицы к безопасной записи">
      <Lead>Подготовленная таблица пользователей ещё не объясняет вызывающему коду причину отказа. Добавим операции хранения и разберём конфликт двух попыток.</Lead>
      <LinkedNotes items={[
        { title: "Точка старта", description: "Для работы нужна проверенная User-схема, общее правило login и две учебные копии с историей. Если результата пока нет, сначала завершаем подготовку схемы." },
        { title: "Новый вопрос", description: "Две попытки прочитали, что имя свободно. Могут ли обе сохранить пользователя с этим именем?" },
        { title: "Результат", description: "Напишем создание и поиск User, вернём безопасные поля, различим конфликт имени и восстановим Session после rollback." },
      ]} />
      <p>Чтение показывает состояние в момент запроса. Ограничение таблицы проверяет допустимость самой записи. Эти операции отвечают на разные вопросы.</p>
    </Section>
    <Section number="01" title="Запись внутри транзакции и сохранённая строка">
      <Lead>Восстановим механизм на каталоге меток. У метки есть уникальный code, вес и внутреннее примечание. Это отдельный опыт без User и паролей.</Lead>
      <p>Connection соединяет программу с файлом БД. SQLAlchemy Session ведёт ORM-объекты и транзакцию. Это не сессия входа пользователя. Как и у Task-repository, Session передаётся репозиторию снаружи.</p>
      <p>Сначала задаём запрос. Условие where отбирает нужный code. Метод scalars извлекает ORM-объекты из результата; one_or_none возвращает единственный объект или None. Если совпадений несколько, он сообщает об ошибке, а не скрывает её выбором первой строки.</p>
      <CodeBlock caption="Фрагмент внутри открытой reading с подготовленной таблицей labels" code={'statement = select(LabelRow).where(LabelRow.code == "kept")\nrow = reading.scalars(statement).one_or_none()\nif row is not None:\n    public = {"id": row.id, "code": row.code}\nelse:\n    public = None'} />
      <p>Словарь public содержит выбранные значения. В нём нет внутреннего note. ORM-объект остаётся внутри открытого scope. Вызов <code>reading.get(LabelRow, id)</code> ищет по первичному ключу и тоже возвращает None при отсутствии строки. Сначала проверяем None, затем читаем поля.</p>
      <p>Add передаёт объект Session. Flush отправляет SQL внутри транзакции: может выдать id и уже может получить отказ ограничения. Только commit подтверждает результат. Rollback отменяет неподтверждённые изменения. Поэтому полученный после flush id ещё не доказывает сохранение.</p>
      <CodeBlock caption="Фрагмент отмены: label и Factory определены в полном опыте ниже" code={'with Factory() as session:\n    candidate = label("cancelled")\n    session.add(candidate)\n    session.flush()\n    saved_id = candidate.id\n    session.rollback()\n\nwith Factory() as verification:\n    print(verification.get(LabelRow, saved_id) is None)\n# True'} />
      <p>Новая Session видит отсутствие отменённой строки. With Factory закрывает Session при выходе, но сам по себе не обещает commit. Владелец операции подтверждает успех или откатывает отказ, включая ошибку commit. Репозиторий не должен скрыто подтверждать часть составной операции.</p>
      <p>Безопасную копию собираем внутри scope, пока атрибуты доступны. После commit ORM-атрибуты обычно требуют повторной загрузки. Поэтому наружу передаём скопированные значения, а не закрытый ORM-объект. У User в такой копии только id/login; hash и role остаются внутренними.</p>
      <Callout><strong>Малая проба перед проектом.</strong> В полном опыте найдём отдельную запись cancelled. Предскажем результат замены её rollback на commit и проверим на новом запуске. Свежая Session увидит эту строку. Эта проба различает подтверждение и отправку SQL, но ещё не заменяет собственный User-repository.</Callout>
    </Section>
    <Section number="02" title="Два отсутствия не резервируют имя">
      <Lead>Code draft отсутствует. A и B успевают прочитать None до первой записи. Ни одно чтение не резервирует code.</Lead>
      <p>Откроем два разных engine.connect одного файлового SQLite и привяжем отдельные Session через <code>Factory(bind=connection)</code>. Две переменные одной Session не дают независимых соединений. Оба read-scope закончим rollback и закроем до записей.</p>
      <Diagram caption="Два соединения, один файл. Сначала оба чтения, затем записи A и B.">
        <DiagramLane title="Соединение A"><DiagramFlow label="Проверка и первая запись">
          <DiagramNode title="1. Чтение A">draft отсутствует</DiagramNode><DiagramArrow label="чтение закончено" />
          <DiagramNode title="3. Запись A">insert draft и commit</DiagramNode>
        </DiagramFlow></DiagramLane>
        <DiagramLane title="Соединение B"><DiagramFlow label="Независимая проверка и вторая запись">
          <DiagramNode title="2. Чтение B">Тоже None, до записи A</DiagramNode><DiagramArrow label="после commit A" />
          <DiagramNode title="4. Запись B">UNIQUE отклоняет draft</DiagramNode>
        </DiagramFlow></DiagramLane>
        <DiagramNote title="Общая БД">До commit A строк draft: 0. После commit A: 1. После отказа B: 1. Данные первой попытки не заменены второй.</DiagramNote>
      </Diagram>
      <p>Это управляемое чередование в одном процессе. A подтверждает запись до того, как B пишет. Если оставить первую запись незавершённой, вместо UNIQUE можно получить блокировку. Это иной исход; он не доказывает, что имя занято.</p>
      <CodeSequence title="Восстановим опыт двух попыток" prompt="Оба клиента хотят draft. Сначала получим два None, затем проверим ограничение БД. Внутри объединённых шагов действия уже названы по порядку. Соберём весь опыт."
        pieces={[
          { id: "second", code: "B записывает draft и получает UNIQUE" },
          { id: "finish", code: "Закончить оба чтения и закрыть read-Session" },
          { id: "checks", code: "A читает None; B независимо читает None" },
          { id: "rollback", code: "Откатить отказ B перед новой операцией" },
          { id: "first", code: "A записывает draft и подтверждает commit" },
        ]} correctOrder={["checks", "finish", "first", "second", "rollback"]}
        explanation="Два None не резервируют code. После завершения чтений A подтверждает строку. B доходит до UNIQUE, а rollback возвращает его Session к новой работе."
        incorrectExplanation="Сверим границы: оба чтения идут до записи A; чтения заканчиваются до write-scope; B пишет после commit A; после отказа нужен rollback. Предварительный поиск второй insert не заменяет." />
      <QuizCard question="Теперь A пишет draft, B пишет review. Чтения закончены, записи подтверждаются по очереди. Какой итог?"
        options={["Обязательно конфликт: соединений два", "Сохранятся обе разные метки", "Вторая запись заменит первую"]}
        correctIndex={1} explanation="Конфликт возникает из-за одинакового уникального значения. Два соединения сами по себе не мешают последовательно сохранить разные code." />
      <p>Если поменять победителя, после одинаковых попыток всё равно останется одна строка, но её данные принадлежат первой подтверждённой записи. Это проверим и для эквивалентных login.</p>
    </Section>
    <Section number="03" title="Различаем причину и восстанавливаем Session">
      <Lead>После отказа нужно понять его причину и вернуть возможность новой транзакции. Это две разные задачи.</Lead>
      <p>IntegrityError означает нарушение целостности, а не обязательно занятое имя. В <code>error.orig</code> находится исходная ошибка SQLite. Атрибут sqlite_errorcode уточняет класс: UNIQUE, CHECK и другие ограничения различаются. <code>getattr(original, "sqlite_errorcode", None)</code> даёт None, если признака нет. Отсутствие признака не подтверждает конфликт.</p>
      <p>В опыте сравним extended code SQLITE_CONSTRAINT_UNIQUE и точное сообщение о labels.code. Это узкий адаптер проверенного драйвера, а не универсальный формат всех БД. Неизвестная сигнатура остаётся отдельной ошибкой хранения. Не ищем слово UNIQUE в полном SQLAlchemy traceback: там бывают SQL и параметры, которые не должны попасть в ответ или журнал.</p>
      <p>После failed flush Session требует явный rollback, хотя драйвер уже отменил неудачную транзакцию. Продолжение без rollback даёт PendingRollbackError. В полном опыте наблюдаем его специально; обычный прикладной код сразу откатывает отказ. После отката сохраним review именно в той же B-Session.</p>
      <p>Создадим каталог examples, если его нет, и сохраним полный файл <code>examples/unique_label_probe.py</code> в проекте. Занятый файл не перезаписываем: выбираем свободный суффикс и меняем команду. Нужны уже имеющийся SQLAlchemy и стандартная библиотека, без нового сервера и установки пакетов.</p>
      <p>TemporaryDirectory создаёт новый каталог внутри текущего project и удаляет его после опыта. Path задаёт SQLite-файл, URL.create строит URL. Подготовка Base/LabelRow создаёт только labels этого опыта; реальная схема Planner создаётся историей миграций. После setup проверяем существование файла до дальнейшего открытия. В конце закрываются Session и Connection, затем dispose освобождает engine, затем удаляется временный каталог.</p>
      <CodeBlock caption="Полный независимый опыт: examples/unique_label_probe.py" code={labelProbe} />
      <CodeBlock caption="Запуск из project тем же Python" code="python examples/unique_label_probe.py" />
      <CodeBlock caption="Результат исходного полного файла" code={"found: kept\nby id: kept\nmissing: None\ncancelled absent: True\ntwo checks: None None\nrollback required: True\ncode conflict: True\nCHECK is code conflict: False\nsaved: ['draft', 'kept', 'review']\nfirst attempt kept: True\npublic copy: kept"} />
      <p>Два None получены до insert. Code conflict относится только к labels.code. Weight=7 использует свободный code и нарушает CHECK: False доказывает, что классификатор не переименовал любой отказ в конфликт. В saved есть review: после rollback именно B-Session смогла подтвердить новую запись. Её наличие и данные первой draft проверены свежим чтением.</p>
      <p>Наблюдение можно превратить в проверку: <code>assert verification.get(LabelRow, cancelled_id) is None</code> останавливает сценарий, если отменённая строка есть. Assert проверяет условие, а не исправляет БД. Так же сравним сохранённые до изменения Task-поля с финалом.</p>
      <Callout><strong>Перенос с меньшей подсказкой.</strong> Сначала предскажем итог, если первой пишет B. В копии опыта поменяем соединения только у двух write-scope, сохранив оба исходных None и первый commit до второй записи. Должна остаться одна draft с данными нового победителя. Отдельно добавим новую допустимую метку после rollback ошибочного weight и проверим её новой Session.</Callout>
      <p>Для User используем одно normalize_login: trim краёв, ASCII lowercase, 3–32 символа из [a-z0-9_]. Внутренний пробел и Unicode недопустимы. Создание и поиск применяют одно правило. Предикат ошибки подтверждает UNIQUE именно users.login; репозиторий переводит его в LoginAlreadyExists, владелец Session откатывает любой отказ. Наружу копируются id/login. Пароль не принимается и не преобразуется.</p>
      <p>В Planner дополняем существующий модуль репозитория, сохраняя Task-операции. Create выполняет flush, не commit. Иначе будущая составная операция потеряет единую границу транзакции. Понятное сообщение «Имя уже используется» показываем после подтверждения причины и rollback. BUSY, CHECK и неизвестный сбой остаются другими отказами, без автоповтора.</p>
      <p>Перед переносом восстановим подготовку корневого проверочного скрипта. Load_settings читает прежнюю настройку DATABASE_URL; make_url разбирает её, свойство database содержит путь. Здесь явно выбран адрес одноразовой копии из нашей записи подготовки схемы. Исходник и backup не являются целями. Проверка is_file идёт до подключения: отсутствующая БД не должна тихо создаться. При смене цели останавливаем прежний API и применяем настройку заново.</p>
      <CodeBlock caption="Подготовка внутри project/check_user_repository.py: только существующая согласованная User-копия" code={'from pathlib import Path\nimport os\nfrom sqlalchemy import text\nfrom sqlalchemy.engine import make_url\nfrom app.core.config import load_settings\nfrom app.core.engine import make_engine\nfrom app.DBase.session import create_session_factory\n\nif "DATABASE_URL" not in os.environ:\n    raise RuntimeError("Set the explicit disposable DATABASE_URL first")\nsettings = load_settings()\npath = Path(make_url(settings.database_url).database).resolve()\nif not path.is_file():\n    raise FileNotFoundError(path)\nengine = make_engine(settings.database_url)\nFactory = create_session_factory(engine)\ntry:\n    with Factory() as control:\n        version = control.execute(text("SELECT version_num FROM alembic_version")).scalar_one()\n        print("revision:", version)\nfinally:\n    engine.dispose()'} />
      <p>Text обозначает явный SQL-запрос чтения. Scalar_one извлекает единственное значение revision; отсутствие ожидаемой таблицы или строки останавливает подготовку. Этот фрагмент проверяет адрес и метку, но не доказывает саму схему: её согласованность и опись должны быть получены при User-upgrade. Вызовы make_engine и create_session_factory используют существующие модули Planner. Они не выполняют миграции.</p>
      <p>Сохраняем наблюдения до записи. Метод all после scalars получает весь список строк. Из него делаем отдельные значения, чтобы сравнение после изменения не зависело от ORM-ссылок. Например, внутри независимого каталога перед опытом получаем <code>before = [(r.id, r.code, r.weight) for r in session.scalars(select(LabelRow).order_by(LabelRow.id)).all()]</code>. После опыта такой же список для прежних ID должен совпадать с before. Для Task перечисляем все исходные поля, включая копию списка tags; User hash/role сравниваем только в памяти. В README сохраняем безопасные id/login, T0 и обозначение цели, без hash или traceback.</p>
    </Section>
    <Section number="04" title="Что мы будем делать в практике">
      <Lead>Напишем User-repository в том же Planner и собственный проверочный сценарий. Каталог меток остаётся опорой, но не готовыми методами User.</Lead>
      <LinkedNotes items={[
        { title: "Добавим создание и поиск", description: "Реализуем create/get-by-login/get-by-id и проекцию id/login. Две фиктивные записи прочитаем новой Session. Если имя или ID отсутствует, получим None." },
        { title: "Воспроизведём конфликт имени", description: "На двух соединениях сначала увидим отсутствие эквивалентного login, затем одну запись и подтверждённый UNIQUE второй попытки." },
        { title: "Продолжим после отказа", description: "После rollback в той же Session сохраним новую строку. Отличим CHECK от конфликта имени и сверим все исходные задачи на новой и восстановленной копиях." },
      ]} />
      <p>Условие практики задаёт свободные имена, опись и роли двух учебных копий. При сомнении в URL сначала восстанавливаем подготовку схемы. Фиктивные hash-строки не становятся аккаунтами для входа: парольный опыт начнётся на другой новой тестовой базе.</p>
    </Section>
  </RichLesson>;
}
