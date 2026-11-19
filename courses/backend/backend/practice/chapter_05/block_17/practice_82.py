"""Project practice: initial immutable Task history on two new SQLite files."""

_BASELINE = '''from alembic import op
import sqlalchemy as sa

revision = "p0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("title_normalized", sa.String(), nullable=False),
        sa.CheckConstraint("id > 0", name="ck_tasks_id_positive"),
        sa.CheckConstraint("priority >= 1 AND priority <= 5", name="ck_tasks_priority_range"),
    )

def downgrade():
    raise RuntimeError("Removing the baseline is not supported")
'''

_RESERVE = '''# Python из корня project, после сверки трёх защищённых адресов с описью.
from pathlib import Path
from sqlalchemy.engine import URL

e1 = Path("data/baseline-new-a.sqlite3").resolve()
e2 = Path("data/baseline-new-b.sqlite3").resolve()
e1.touch(exist_ok=False)
print("E1", e1, URL.create("sqlite", database=e1.as_posix()))
e2.touch(exist_ok=False)
print("E2", e2, URL.create("sqlite", database=e2.as_posix()))
'''

_CHECK_SCHEMAS = '''# Python из корня project, после upgrade каждой цели.
# При выборе других свободных имён ниже стоят записанные фактические имена.
from pathlib import Path
import sqlite3

for name in ("baseline-new-a.sqlite3", "baseline-new-b.sqlite3"):
    path = (Path("data") / name).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path)
    try:
        print(name)
        print(db.execute("PRAGMA table_info(tasks)").fetchall())
        print(db.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='tasks'").fetchone()[0])
        print(db.execute("PRAGMA index_list(tasks)").fetchall())
        print(db.execute("PRAGMA foreign_key_list(tasks)").fetchall())
        print(db.execute("SELECT type, name FROM sqlite_schema WHERE type IN ('table', 'index', 'trigger', 'view') ORDER BY type, name").fetchall())
        print(db.execute("SELECT version_num FROM alembic_version").fetchall())
        valid = (2001, "Baseline check", 5, 0, '["baseline-check"]', "baseline check")
        db.execute("INSERT INTO tasks (id, title, priority, is_done, tags, title_normalized) VALUES (?, ?, ?, ?, ?, ?)", valid)
        if name == "baseline-new-a.sqlite3":
            db.commit()
        else:
            db.rollback()
        try:
            bad = (2002, "Baseline check", 0, 0, '["baseline-check"]', "baseline check")
            db.execute("INSERT INTO tasks (id, title, priority, is_done, tags, title_normalized) VALUES (?, ?, ?, ?, ?, ?)", bad)
            db.commit()
        except sqlite3.IntegrityError as error:
            print("priority=0:", error)
            db.rollback()
        try:
            missing = (2003, "Baseline check", 0, '["baseline-check"]', "baseline check")
            db.execute("INSERT INTO tasks (id, title, is_done, tags, title_normalized) VALUES (?, ?, ?, ?, ?)", missing)
            db.commit()
        except sqlite3.IntegrityError as error:
            print("priority отсутствует:", error)
            db.rollback()
        print(db.execute("SELECT id, title, priority, is_done, tags, title_normalized FROM tasks ORDER BY id").fetchall())
    finally:
        db.close()
'''

_READ_REPEAT = '''# Новый Python-процесс из корня project, после повтора upgrade для E1 и E2.
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

for name in ("baseline-new-a.sqlite3", "baseline-new-b.sqlite3"):
    path = (Path("data") / name).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    engine = create_engine(URL.create("sqlite", database=path.as_posix()))
    try:
        with Session(engine) as session:
            print(name)
            print(session.execute(text("SELECT version_num FROM alembic_version")).scalars().all())
            print(session.execute(text("SELECT id, title, priority, is_done, tags, title_normalized FROM tasks ORDER BY id")).all())
    finally:
        engine.dispose()
'''

MANUAL_PRACTICE = {
    82: [
        {
            "title": "Исходная история Planner",
            "task": "Работаем в своём project после проверенного восстановления. Рядом есть app/DBase/models.py с TaskRow, исходная schema/data-опись, согласованный резерв и отдельная заполненная восстановленная копия. Их фактические имена берём из своей описи, не из книжного опыта. Перед началом показываем совпавшие задачи и URL копии; при расхождении возвращаемся к восстановлению.",
            "tasks": [
                {
                    "title": "Два новых адреса отделены от сохранённых файлов",
                    "task": "Нам нужны две независимые пустые цели для истории. Из корня project выберем data/baseline-new-a.sqlite3 (E1) и data/baseline-new-b.sqlite3 (E2), сравним их абсолютные адреса с исходником, резервом и восстановленной копией. Каждый отсутствовавший файл зарезервируем исключительно. При занятом имени сохраним его и выберем новый свободный суффикс. После резервирования первого файла запишем его состояние: не запускаем создание первой цели заново, если занятой оказалась только вторая. В существующий README.md добавим раздел «Исходная история схемы» с пятью ролями, фактическими URL и местом исходной описи. Соединения и писатели к целям закрыты.",
                    "requirements": [
                        "README содержит разные абсолютные адреса исходника, резерва, восстановленной копии, E1 и E2; источник исходной описи указан.",
                        "E1 и E2 созданы по ранее свободным именам исключительно, без пользовательских таблиц и метки; ни один защищённый или занятый файл не заменён.",
                        "Повторное резервирование уже занятой цели отклоняется; её состояние и байты не меняются.",
                    ],
                    "result": "Есть два собственных новых пустых файла и запись их ролей/URL. Занятое имя даёт отказ, сохранённые файлы прежние.",
                    "hint": "Вернёмся к разделу о пустой цели: resolve даёт адрес для сравнения, touch с exist_ok=False защищает занятое имя. Сначала назовём роль каждого файла.",
                    "answer": {
                        "explanation": "Новая цель не является очищенной копией. В теории тот же способ создавал отдельный books.sqlite3; здесь сохраняем все прежние ресурсы Planner и выбираем два свободных имени.",
                        "steps": [
                            "Прочитаем свою опись и выпишем реальные адреса трёх сохранённых файлов. Проверим, что восстановленные ID и все поля совпали до начала работы.",
                            "Из project получим абсолютные E1/E2. Если адрес занят или совпал с защищённым, выберем другой суффикс до touch; подставим фактическое имя во все следующие карточки.",
                            "Выполним показанный Python-код по шагам. После каждого успешного touch запишем роль и URL. Если второй отказал, первый уже зарезервирован, его повторно не создаём.",
                            "Повторим touch существующей цели отдельно: FileExistsError означает ожидаемый отказ до подключения. В README сохраним адреса и первоначальное отсутствие таблиц/метки.",
                        ],
                        "code": _RESERVE,
                        "checks": [
                            "Повтор e1.touch(exist_ok=False) даёт FileExistsError, а не успешное повторное создание.",
                            "Пять адресов различимы; E1/E2 новые, а исходная опись и заполненная копия доступны по прежним адресам.",
                        ],
                    },
                },
                {
                    "title": "Проектная конфигурация указывает на E1",
                    "task": "Теперь создадим историю рядом с приложением. Из корня project настроим стандартное окружение Alembic в migrations, добавим alembic==1.20.1 в существующий requirements.txt без изменения прежних pins. Используем согласованный venv с SQLAlchemy 2.0.55. В alembic.ini укажем script_location и абсолютный URL E1 из README. Стандартный env.py прочитаем по объяснённому online-пути; target_metadata остаётся None, приложение и init_db не импортируются. Неожиданную существующую историю сначала выясняем, поверх неё init не запускаем. Проверим, что heads пока пуст, а пользовательских таблиц ещё нет.",
                    "requirements": [
                        "В project созданы alembic.ini, migrations/env.py, script.py.mako, README и пустой versions. Alembic 1.20.1 доступен тому же Python; прежние зависимости сохранены.",
                        "Script_location указывает на %(here)s/migrations; sqlalchemy.url содержит абсолютный URL E1. Логирующие секции сохранены, env.py стандартный с target_metadata=None.",
                        "ENV приложения и DATABASE_URL не приняты за автоматическую настройку Alembic; приложение и init_db при миграционной настройке не запускаются.",
                        "Heads не показывает ревизий; E1/E2 ещё без tasks и метки.",
                    ],
                    "result": "У проекта есть собственная настроенная миграционная среда; она пока не содержит ни ревизий, ни созданной tasks.",
                    "hint": "В разделе настройки посмотрим роли ini и env.py. Фактический адрес Alembic задаёт sqlalchemy.url, не DATABASE_URL приложения.",
                    "answer": {
                        "explanation": "Переносим знакомую настройку из книжного каталога в корень проекта, сохраняя один стандартный шаблон. Собственная история пока пуста.",
                        "steps": [
                            "Проверим python --version и python -m pip --version. В согласованном venv python -m alembic --version показывает 1.20.1; при отсутствии пакета используем объяснённую установку, не меняя Python проекта.",
                            "Добавим строку alembic==1.20.1 в requirements.txt, сохраним остальные строки. Из project выполним python -m alembic init migrations по свободному пути.",
                            "В секции [alembic] поставим script_location = %(here)s/migrations, а в sqlalchemy.url вставим точный полный URL E1, уже напечатанный и записанный в первой карточке. Это собственное вычисленное значение, а не пример чужого пути.",
                            "Оставим стандартные logging, prepend_sys_path и path_separator; в env.py сохраним target_metadata=None и штатные импорты. Прочитаем online-цепочку до run_migrations.",
                            "Выполним python -m alembic heads: отсутствие вывода ревизий здесь ожидаемо. Запишем выбранную цель; пользовательская tasks ещё не создавалась.",
                        ],
                        "code": "# Терминал из project\npython -m alembic --version\npython -m alembic init migrations\npython -m alembic heads\n\n# requirements.txt: добавленная строка\nalembic==1.20.1\n\n# alembic.ini, секция [alembic]\nscript_location = %(here)s/migrations\n# sqlalchemy.url получает полный напечатанный URL E1 из первой карточки.",
                        "checks": [
                            "Версия Alembic равна 1.20.1, heads пуст, versions не содержит .py-ревизий.",
                            "Чтение sqlalchemy.url показывает E1; изменение DATABASE_URL приложения не меняет эту строку ini.",
                        ],
                    },
                },
                {
                    "title": "Исходная Task-ревизия написана явно",
                    "task": "По app/DBase/models.py на исходном состоянии и совпавшей описи сами запишем корневую ревизию p0001 с описанием tasks baseline. После генерации файла migrations/versions/p0001_tasks_baseline.py заполним upgrade явным созданием исходной tasks. Сохраним id/title/priority/is_done/tags/title_normalized, обязательность колонок, ключ и оба именованных CHECK. Python-defaults priority=2, is_done=False, tags=list остаются в модели, SQL-defaults не добавляются. Перед применением прочитаем ревизию целиком и сверим history/heads. Никакие данные Planner пока не переносим.",
                    "requirements": [
                        "Файл p0001_tasks_baseline.py задаёт revision='p0001', down_revision=None и единственный root/head.",
                        "Upgrade создаёт tasks: id Integer PK NOT NULL; title String NOT NULL; priority Integer NOT NULL; is_done Boolean NOT NULL; tags JSON NOT NULL; title_normalized String NOT NULL.",
                        "Заданы ck_tasks_id_positive с id > 0 и ck_tasks_priority_range с priority >= 1 AND priority <= 5. SQL-defaults, User/owner/description, дополнительные UNIQUE/FK/индексы, Boolean/JSON CHECK не добавлены.",
                        "Ревизия использует явные операции, не импортирует TaskRow/приложение и не вызывает изменяемую metadata/create_all. Исходные модели и init_db сохранены; downgrade явно отказывает без удаления схемы.",
                        "До upgrade E1/E2 ещё без tasks и метки; наличие файла истории не объявлено применением.",
                    ],
                    "result": "Есть собственный проверенный файл исходного DDL; history/heads показывают p0001, а база ещё не изменена этой ревизией.",
                    "hint": "В полной книжной ревизии посмотрим форму op.create_table и именованного CheckConstraint. В сравнении defaults найдём, что не должно превратиться в server_default.",
                    "answer": {
                        "explanation": "Ниже полный вариант ревизии для исходной схемы. Он содержит снимок DDL, а Python-defaults остаются в существующем TaskRow. После принятия этот смысл не переписывается вслед за моделью.",
                        "steps": [
                            "Из project выполним python -m alembic revision --rev-id p0001 -m \"tasks baseline\". Генерация создаёт файл, но не таблицу.",
                            "В файле зададим корневые идентификаторы и напишем upgrade как явный create_table. Каждую из шести колонок сопоставим с моделью и исходной описью, включая tags и title_normalized.",
                            "Добавим оба именованных CHECK отдельно от колонок. Не заменяем Python default на server_default: в исходной SQLite SQL DEFAULT отсутствуют.",
                            "Проверим отсутствие вызовов приложения/metadata и лишних объектов; сохраним явный отказ downgrade. Выполним history и heads, получим один корневой p0001 без применения к базе.",
                        ],
                        "code": _BASELINE,
                        "checks": [
                            "Команды python -m alembic history и heads показывают один p0001 с отсутствующим родителем.",
                            "В файле шесть нужных колонок, два CHECK, нет server_default и обращения к будущей модели. Table_info и version на E1/E2 ещё не утверждаются как применённые.",
                        ],
                    },
                },
                {
                    "title": "Одна история воспроизводит две схемы",
                    "task": "Перед применением ещё раз прочитаем sqlalchemy.url и роль E1. Выполним upgrade head, новым соединением сравним структуру с исходной описью. Закроем соединения, явно переключим ini на E2 и повторим той же ревизией без её редактирования. В обеих целях проверим колонки, PK/nullable/defaults, CHECK, индексы/FK и остальные объекты. На E1 отдельно сохраним строку id=2001, title='Baseline check', priority=5, is_done=0, tags=['baseline-check'] в JSON-тексте, title_normalized='baseline check'. На E2 допустимую пробу откатим, чтобы она осталась пустым эталоном. В каждой БД проверим новый id=2002 со всеми остальными допустимыми полями и priority=0, затем отдельный id=2003 с пропущенным priority: ожидаем разные отказы и после каждого rollback. Запишем наблюдения до повтора в README. Исходник, резерв и заполненная копия не являются целью.",
                    "requirements": [
                        "E1/E2 получены upgrade одной p0001 и соответствуют исходной описи по всем шести колонкам, PK/NOT NULL, отсутствию SQL-defaults, обоим именованным CHECK и составу других объектов; одинаково неверные схемы не приняты.",
                        "В каждой alembic_version ровно p0001; история не подменена create_all+stamp.",
                        "На E1 commit сохраняет ровно контрольную строку 2001 со всеми указанными полями; E2 не содержит Task-строк после проб.",
                        "Уникальный положительный id=2002 с priority=0 отвергнут ck_tasks_priority_range. Пропуск priority у id=2003 отвергнут NOT NULL. После отдельных rollback ошибочных строк нет, строка 2001 E1 сохранена.",
                        "README содержит фактические URL, schema-наблюдения, метки и значения строк до повтора. Заполненная копия не получила метку; исходные файлы не заменены.",
                    ],
                    "result": "Две новые SQLite имеют исходную схему и p0001. E1 хранит контрольную строку, E2 пуста, CHECK и отсутствие SQL-default различены наблюдаемыми отказами.",
                    "hint": "Вернёмся к чтению одной строки PRAGMA и SQL ограничения. Для ошибочной вставки сначала проверим свободный положительный id и допустимость всех полей кроме проверяемого.",
                    "answer": {
                        "explanation": "Сначала применяем и сравниваем структуру, затем проверяем действия с данными. Вставки с разными id отделяют отказ CHECK от конфликта ключа; пропуск priority отдельно проверяет отсутствие серверной подстановки.",
                        "steps": [
                            "При URL E1 в ini из project выполним python -m alembic upgrade head. Прочитаем schema и метку; сравним с описью, а не только сообщением команды.",
                            "После закрытия соединений вставим в ini фактический URL E2 из README и повторим upgrade head без изменений файла ревизии. Прочитаем те же признаки, получим p0001 и исходную tasks.",
                            "В новом Python из project выполним показанный блок проверки, подставив выбранные имена целей при отличии. PRAGMA даёт колонки/PK/defaults, sqlite_schema даёт имена и SQL CHECK; общий список объектов выявляет неожиданное дополнение.",
                            "На E1 допустимую запись commit сохраняет, на E2 rollback оставляет пустую tasks. Затем каждая ошибочная вставка получает свой rollback; строки с 2002/2003 не появляются.",
                            "Разберём сообщения: CHECK constraint failed: ck_tasks_priority_range и NOT NULL constraint failed: tasks.priority. Иной отказ не принимаем за нужное доказательство. Запишем исходные значения строки и результаты перед повтором.",
                        ],
                        "code": _CHECK_SCHEMAS,
                        "checks": [
                            "В table_info все NOT NULL=1; у id PK=1, SQL-default None у всех шести колонок. В SQL tasks присутствуют оба исходных именованных CHECK; дополнительных пользовательских объектов нет.",
                            "Ошибка priority=0 называет ck_tasks_priority_range; ошибка пропущенного priority называет tasks.priority. После rollback E1 содержит только полную строку 2001, E2 пуста.",
                        ],
                    },
                },
                {
                    "title": "Повтор и новая Session подтверждают сохранённое состояние",
                    "task": "До повтора используем записанные в README метки и все шесть значений строки E1. Явно выбирая по очереди E1 и E2 в ini, повторим upgrade head, закроем старые соединения. В новом Python-процессе новой Session прочитаем version и строки обеих целей и сравним с наблюдениями до повтора. Обычным ранее освоенным чтением Planner по URL восстановленной копии сверим её задачи с исходной описью; init_db и модели пока прежние. В README оставим команды воспроизведения, p0001 и финальную цель ini E2. На различающем книжном случае объясним, почему одна метка после stamp не доказывала бы этот результат.",
                    "requirements": [
                        "Повтор upgrade при той же истории не изменил схемы, единственную p0001 и все шесть значений строки 2001 E1; E2 осталась пустой.",
                        "Метка и строки прочитаны новой Session в новом Python-процессе по двум записанным URL; память прежней Session не использована как доказательство.",
                        "Обычное чтение восстановленного Planner по прежнему явному URL даёт исходные ID/поля; заполненная копия без миграционной метки, исходник и резерв сохранены.",
                        "README задаёт корневой p0001, две новые цели, результаты сравнения и команды; финальный ini указывает на E2. Проверенная baseline передаётся без последующего переписывания.",
                    ],
                    "result": "История воспроизводима и повтор безопасен для проверенных состояний. Передаются p0001, чистый E2, проверочный E1 и отдельно сохранённая заполненная копия без метки.",
                    "hint": "В разделе новой Session различим scalars().all() для одной колонки и all() для полной строки. Сравнение требует записанных значений до повтора.",
                    "answer": {
                        "explanation": "Ревизия уже применена, поэтому upgrade при прежнем head не выполняет CREATE заново. Новое чтение проверяет состояние на диске, а отдельное сравнение схемы не заменяется меткой.",
                        "steps": [
                            "При URL E1 повторим python -m alembic upgrade head. После закрытия соединений сменим ini на URL E2 и повторим ту же команду. Историю не редактируем.",
                            "Откроем новый Python-процесс из project и выполним показанный код. В каждой цели ожидаем ['p0001']; E1 даёт одну строку с прежними шестью значениями, E2 даёт пустой список.",
                            "Сравним результат с README до повтора. JSON-текст tags сравниваем как записанное значение, не выдаём сохранение одного количества строк за сохранение содержимого.",
                            "По URL заполненной восстановленной копии выполним обычное ранее проверенное чтение Planner и сверим ID/все поля с исходной описью. Этот URL не ставим целью CREATE или stamp.",
                            "Запишем финальный URL E2 в ini и README. Копия подключается к истории только в следующей теме после отдельного сравнения с образцом E2; будущий head не заменяет конкретную исходную p0001.",
                        ],
                        "code": _READ_REPEAT,
                        "checks": [
                            "Для E1 новая Session читает ['p0001'] и прежнюю контрольную строку 2001; для E2 ['p0001'] и [].",
                            "Пустой книжный файл после stamp имел бы метку без book_stock: это не эквивалент пройденному upgrade и проверке схемы.",
                            "Восстановленные задачи совпадают с исходной описью, а заполненная копия ещё без alembic_version. README и ini однозначно задают текущие роли.",
                        ],
                    },
                },
            ],
            "result": "У Planner есть явная исходная p0001, воспроизведённая на двух новых SQLite с исходными ограничениями и defaults. Заполненная восстановленная копия сохранена отдельно и пока без метки.",
        }
    ]
}

CODE_TASKS = {}
