"""Project practice for schema expansion."""

MANUAL_PRACTICE = {'84.1': [{'title': 'Расширяем две учебные базы',
           'task': 'Мы работаем с двумя изолированными копиями Planner. Одна база создана через исходную '
                   'ревизию, другая сверена с ней и содержит синтетические задачи. Добавим необязательное '
                   'описание и убедимся, что история и старые данные согласованы.',
           'tasks': [{'title': '1. Проверим адреса и исходное состояние',
                      'task': 'Прежде чем менять схему, найдём обе учебные базы и исходный резерв в '
                              'изолированной копии project/. По очереди выберем каждую базу в настройках '
                              'Alembic, покажем разрешённые пути и сверим исходную ревизию и состояние '
                              'данных. Настроим DATABASE_URL приложения на ту же копию. Исходный резерв восстановим в новый свободный путь и сверим с описью. Отдельно попробуем неверный адрес, чтобы увидеть остановку до '
                              'подключения. Если резерв не восстанавливается или схема неизвестна, дальше не '
                              'идём.',
                      'requirements': ['Адреса пустой базы baseline, заполненной копии и исходного резерва '
                                       'определены в изолированной учебной папке; существование файлов '
                                       'подтверждено.',
                                       'Для каждой выбранной базы sqlalchemy.url и абсолютный '
                                       'expected_database_path согласованы до current; разрешённые пути '
                                       'совпадают, DATABASE_URL приложения указывает на ту же базу.',
                                       'Исходная revision сопоставлена с файлом baseline; структура и '
                                       'ограничения сверены с описью, сохранены число строк и значения id, '
                                       'title, priority, is_done, tags, title_normalized.',
                                       'На отдельной пробе изменение только URL при прежнем expected path '
                                       'вызывает отказ до появления ошибочного файла; проверено '
                                       'восстановление исходного резерва в новый путь.'],
                      'result': 'Оба учебных адреса, исходная ревизия, данные до изменения и восстановимость '
                                'резерва проверены. Отрицательный случай не создаёт новый файл.',
                      'hint': 'Вернёмся к проверке двух путей и различию current и heads. Метка не заменяет '
                              'сравнение схемы.',
                      'answer': {'explanation': 'Alembic использует sqlalchemy.url для соединения, а expected_database_path служит проверкой '
                'адреса. Их равенство нужно установить до подключения.',
 'steps': ['Для каждой копии выберем путь по описи, проверим существование и установим оба значения в '
           'alembic.ini.',
           'Сравним разрешённые абсолютные пути; настроим DATABASE_URL приложения на ту же копию.',
           'Вызовем python -m alembic current и python -m alembic heads, сопоставим результат с реальным '
           'файлом baseline и запишем исходные значения строк.',
           'На отдельном пути изменим только sqlalchemy.url. Убедимся, что команда отказала и неверный файл '
           'не появился. Восстановим согласованную пару.',
           'Проверим восстановление исходного snapshot в новый свободный путь через python -m '
           'app.DBase.backup_snapshot SOURCE DEST и сравним revision, схему и все сохранённые строки.',
           'Сохраним в описи реальные пути обеих баз, исходного резерва и восстановления, а также результаты '
           'чтения структуры и строк. Позже используем именно эти выбранные пути.'],
 'code': 'python -m alembic current\n'
         'python -m alembic heads\n'
         '\n'
         '# В PowerShell заменим оба обозначения реальными путями из описи:\n'
         'python -m app.DBase.backup_snapshot "<путь исходного резерва>" "<новый свободный путь '
         'восстановления>"',
 'checks': ['Исходный резерв не является целью миграции.',
            'При несовпадении путей команда не подключается и не создаёт ошибочную базу.']}},
                     {'title': '2. Подготовим кандидат ревизии',
                      'task': 'Теперь подготовим необязательное описание для задач. Изменим существующую '
                              'модель, затем на отдельной базе после baseline попросим Alembic предложить '
                              'ревизию. Файл кандидата ещё не меняет таблицу: сначала прочитаем его '
                              'родительскую ревизию и операции и убедимся, что в историю не попало ничего '
                              'лишнего.',
                      'requirements': ['В app/DBase/models.py определено nullable-поле TaskRow.description с '
                                       'текстовым типом; старые поля и публичные модели API/CLI сохранены.',
                                       'Для отдельной baseline-копии согласованы оба пути; создан и прочитан '
                                       'кандидат revision --autogenerate с сообщением add task description.',
                                       'down_revision совпадает с фактическим ID baseline; upgrade содержит '
                                       'только добавление nullable-колонки без server default, исходная '
                                       'ревизия и ограничения не изменены.'],
                      'result': 'В metadata есть nullable-поле; кандидат добавляет только нужную колонку, а '
                                'существующая таблица ещё не изменена.',
                      'hint': 'Сравним ожидаемую модель и фактическую таблицу. Новый файл истории пока не '
                              'применён.',
                      'answer': {'explanation': 'Изменение модели не изменяет SQLite. Автогенерация предлагает кандидат, который нужно '
                'сравнить с целью и исходной историей.',
 'steps': ['В app/DBase/models.py добавим в TaskRow поле Mapped[str | None] с mapped_column(String, '
           'nullable=True), используя существующие импорты. Так metadata описывает новую колонку, но сама '
           'таблица ещё не меняется.',
           'В alembic.ini выберем существующую отдельную baseline-копию из описи: sqlalchemy.url и '
           'expected_database_path должны указывать на один проверенный файл. В PowerShell из project/ '
           'вызовем python -m alembic current и убедимся, что это исходная ревизия.',
           'В том же PowerShell из project/ выполним python -m alembic revision --autogenerate -m "add task '
           'description". Эта команда создаёт файл кандидата, но не выполняет upgrade.',
           'Откроем созданный файл в migrations/versions/: запишем его revision, а down_revision сравним с '
           'фактическим ID из файла исходной baseline. В upgrade должна остаться только показанная операция '
           'добавления nullable-колонки без server default; лишние операции удалим и не будем менять '
           'исходную ревизию.',
           'В downgrade прочитаем обратную операцию и сверим возможность удаления колонки с версией SQLite. '
           'На рабочих данных downgrade в этом занятии не запускаем.'],
 'code': '# app/DBase/models.py, внутри TaskRow:\n'
         'description: Mapped[str | None] = mapped_column(String, nullable=True)\n'
         '\n'
         '# В upgrade новой ревизии:\n'
         'op.add_column("tasks", sa.Column("description", sa.String(), nullable=True))\n'
         '\n'
         '# В downgrade той же ревизии:\n'
         'op.drop_column("tasks", "description")',
 'checks': ['Никаких новых обязательных значений или server default нет.',
            'В кандидате нет удаления старых полей, CHECK или индексов.']}},
                     {'title': '3. Применим переход к обеим базам',
                      'task': 'Одна учебная база представляет новую установку, в другой находятся две '
                              'прежние синтетические задачи. По очереди применим проверенную ревизию к '
                              'каждой базе, а затем сравним реальную структуру и данные с описью до '
                              'изменения. При неожиданной ошибке остановимся и сохраним признаки для '
                              'диагностики.',
                      'requirements': ['Для каждой базы URL и expected_database_path согласованы и показаны '
                                       'до upgrade head; после команды current указывает новую ревизию.',
                                       'В обеих базах фактическая схема содержит nullable description и '
                                       'прежние ограничения и индексы; пустая база остаётся без строк.',
                                       'В заполненной копии число строк, ID и все шесть старых полей '
                                       'совпадают с исходной описью; у старых строк description IS NULL.',
                                       'При неизвестной ревизии или ошибке операция остановлена без stamp и '
                                       'слепого повтора.'],
                      'result': 'Две базы имеют одинаковую согласованную схему и новую ревизию; старые '
                                'строки не изменились, описание у них NULL.',
                      'hint': 'Перед каждой новой целью меняются оба значения настройки. Проверим содержимое '
                              'базы независимо от вывода команды.',
                      'answer': {'explanation': 'Каждая база проходит собственный переход, хотя использует одни и те же файлы истории.',
 'steps': ['В PowerShell из project/ выберем существующую пустую baseline-базу из описи. В alembic.ini '
           'согласуем sqlalchemy.url и expected_database_path с ней и покажем оба разрешённых пути. Выполним '
           'python -m alembic upgrade head, затем python -m alembic current и сравним ID с кандидатом.',
           'Отдельно переключим оба значения на существующую заполненную копию из той же описи и повторим '
           'upgrade и current. Если ревизия неизвестна или команда отказала, остановимся: не используем '
           'stamp и не повторяем вслепую.',
           'Сохраним код ниже как check_schema.py в project/ и дважды запустим python check_schema.py. На '
           'запрос введём сначала путь пустой базы, затем при втором запуске путь заполненной базы из описи. '
           'Скрипт не создаёт отсутствующий файл.',
           'Для каждой базы сопоставим вывод с её описью до изменения: у прежних колонок сохраняются типы, '
           'NOT NULL, значения по умолчанию и первичный ключ; новая description допускает NULL и не имеет '
           'значения по умолчанию. Сравним SQL объектов, индексы (включая состав и уникальность), внешние '
           'ключи, CHECK и триггеры. В SQL таблицы ожидается добавление description, поэтому буквального '
           'равенства всей строки CREATE TABLE не требуется.',
           'Сравним число строк и шесть старых полей с описью каждой базы: пустая останется пустой, в '
           'заполненной все исходные строки сохранены и их ID присутствуют в выводе description IS NULL. '
           'Совпадение одной метки revision не заменяет проверку структуры.'],
 'code': '# project/check_schema.py\n'
         'from pathlib import Path\n'
         'from contextlib import closing\n'
         'import sqlite3\n'
         '\n'
         'path = Path(input("Путь проверенной учебной базы из описи: ").strip())\n'
         'if not path.is_file():\n'
         '    raise FileNotFoundError(path)\n'
         'with closing(sqlite3.connect(path)) as db:\n'
         '    print("revision:", db.execute("SELECT version_num FROM alembic_version").fetchall())\n'
         '    print("columns:", db.execute("PRAGMA table_xinfo(tasks)").fetchall())\n'
         '    objects = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema "\n'
         '                         "WHERE name NOT LIKE \'sqlite_%\' ORDER BY type, name").fetchall()\n'
         '    print("objects:", objects)\n'
         '    indexes = db.execute("PRAGMA index_list(tasks)").fetchall()\n'
         '    print("indexes:", indexes)\n'
         '    for index in indexes:\n'
         '        print("index details:", index[1],\n'
         '              db.execute("SELECT * FROM pragma_index_xinfo(?)", (index[1],)).fetchall())\n'
         '    print("foreign keys:", db.execute("PRAGMA foreign_key_list(tasks)").fetchall())\n'
         '    print("rows:", db.execute("SELECT id, title, priority, is_done, tags, title_normalized "\n'
         '                              "FROM tasks ORDER BY id").fetchall())\n'
         '    print("descriptions:", db.execute("SELECT id, description FROM tasks ORDER BY id").fetchall())\n'
         '    print("NULL:", db.execute("SELECT id FROM tasks WHERE description IS NULL ORDER BY '
         'id").fetchall())\n',
 'checks': ['Оба фактических файла сверены с описью структуры и строк до изменения, а не только с текущей '
            'revision.',
            'В каждой базе у description нет NOT NULL и значения по умолчанию; старые ограничения, индексы и '
            'остальные объекты сохранены.',
            'Пустая база не содержит строк; в заполненной сохранены все старые поля, а описания старых строк '
            'равны NULL.']}},
                     {'title': '4. Проверим NULL, текст и повтор',
                      'task': 'На изолированной заполненной копии выберем две известные синтетические '
                              'задачи. Одной запишем непустое описание, у другой оставим NULL, затем '
                              'прочитаем обе через новое соединение. После повторного upgrade сравним эти '
                              'значения и старые поля: так мы увидим, сохранилось ли состояние, а не только '
                              'номер ревизии.',
                      'requirements': ['У одной существующей синтетической задачи записан непустой текст, у '
                                       'другой сохранён NULL; изменение подтверждено транзакцией и чтением '
                                       'новым соединением.',
                                       'Для повторного upgrade head согласованы URL и ожидаемый путь; после '
                                       'повтора оба значения, старые поля и число строк сохранены.',
                                       'Несуществующий ID не засчитывается как успешная запись.'],
                      'result': 'После повтора одна задача хранит текст, вторая NULL; старые поля и число '
                                'строк прежние.',
                      'hint': 'Вернёмся к параметризованному UPDATE и различию между NULL и пустой строкой.',
                      'answer': {'explanation': 'Повтор на head не должен заново запускать уже применённую ревизию и менять значения.',
 'steps': ['В project/ сохраним код ниже как update_description.py. Запустим python update_description.py и '
           'введём путь существующей заполненной базы и два разных синтетических ID из описи. Проверка до '
           'записи не позволит принять отсутствующий ID или строку с уже заполненным описанием за нужное '
           'исходное состояние.',
           'Параметризованный UPDATE меняет только выбранный ID; rowcount равен 1. После commit новое '
           'соединение читает текст и NULL второй строки. Сохраним этот вывод и старые поля из '
           'check_schema.py.',
           'В alembic.ini снова сверим оба пути с этой заполненной базой. В PowerShell из project/ выполним '
           'python -m alembic upgrade head и снова запустим check_schema.py для неё. После повтора обе '
           'строки, шесть старых полей и число строк совпадают с сохранёнными значениями.'],
 'code': '# project/update_description.py\n'
         'from pathlib import Path\n'
         'from contextlib import closing\n'
         'import sqlite3\n'
         '\n'
         'path = Path(input("Путь заполненной базы из описи: ").strip())\n'
         'if not path.is_file():\n'
         '    raise FileNotFoundError(path)\n'
         'first = int(input("ID задачи для текста: "))\n'
         'second = int(input("Другой ID, который останется NULL: "))\n'
         'if first == second:\n'
         '    raise ValueError("Нужны разные ID")\n'
         'with closing(sqlite3.connect(path)) as db:\n'
         '    rows = db.execute("SELECT id, description FROM tasks WHERE id IN (?, ?) ORDER BY id",\n'
         '                      (first, second)).fetchall()\n'
         '    if len(rows) != 2 or any(row[1] is not None for row in rows):\n'
         '        raise ValueError("Обе исходные строки должны существовать и иметь NULL")\n'
         '    changed = db.execute("UPDATE tasks SET description = ? WHERE id = ?",\n'
         '                         ("учебное описание", first))\n'
         '    if changed.rowcount != 1:\n'
         '        raise ValueError("Выбранная строка не найдена")\n'
         '    db.commit()\n'
         'with closing(sqlite3.connect(path)) as db:\n'
         '    print(db.execute("SELECT id, description FROM tasks WHERE id IN (?, ?) ORDER BY id",\n'
         '                     (first, second)).fetchall())\n',
 'checks': ['Изменена ровно выбранная синтетическая строка.',
            'NULL и непустой текст сохранились после повтора.']}},
                     {'title': '5. Согласуем создание и проверим прежние интерфейсы',
                      'task': 'Старая инициализация может создать таблицу вне истории. Изменим её в учебной '
                              'копии так, чтобы команда и прямой вызов объясняли нужный путь через Alembic. '
                              'Для каждой новой тестовой базы сначала проверим свободный путь в разрешённой '
                              'папке и согласуем адреса, затем явно создадим пустой файл и применим историю. '
                              'После этого на отдельных тестовых базах проверим исходную и актуальную схемы, '
                              'прежний API и CLI. В конце снимем отдельную копию после расширения и проверим '
                              'восстановление в ещё один свободный путь.',
                      'requirements': ['В app/DBase/init_db.py прежний create_all заменён понятным отказом с '
                                       'инструкцией Alembic; прямой вызов create_schema также не создаёт '
                                       'таблицы.',
                                       'Отказ на отсутствующей и существующей тестовых базах не создаёт файл '
                                       'и не изменяет данные; новая тестовая база создаётся через историю '
                                       'после согласования обоих путей.',
                                       'Историческая проверка применяет конкретную baseline, актуальная '
                                       'проверка применяет head; каждая временная база использует '
                                       'собственные sqlalchemy.url и expected_database_path в Config той же '
                                       'истории.',
                                       'На отдельной копии прежние GET /tasks, POST /tasks, CLI list и add и '
                                       'чтение после перезапуска работают; публичный контракт не расширен '
                                       'полями description и tags.',
                                       'Снимок после расширения создан в новом пути и восстановлен в другой '
                                       'свободный путь; ревизия, схема и данные совпадают, исходный резерв '
                                       'сохранён отдельно.'],
                      'result': 'Есть один штатный путь истории, старые интерфейсы работают, а '
                                'восстановленная после расширения копия совпадает с проверенной базой.',
                      'hint': 'У старой команды проверим и запуск модуля, и прямой вызов. Снимок после '
                              'изменения не заменяет исходный резерв.',
                      'answer': {'explanation': 'Запрет старого обхода и проверка новой истории дополняют друг друга. Резерв до расширения и '
                'снимок после него отвечают на разные вопросы.',
 'steps': ['В app/DBase/init_db.py заменим прежнюю реализацию следующим кодом:\n'
           '\n'
           'def create_schema() -> None:\n'
           '    raise RuntimeError("Используйте python -m alembic upgrade head")\n'
           '\n'
           'if __name__ == "__main__":\n'
           '    create_schema()\n'
           '\n'
           'Импорт не открывает базу; и прямой вызов, и запуск модуля отказывают.',
           'Начнём с одной новой тестовой цели в управляемом временном каталоге: проверим, что её имя '
           'свободно, согласуем sqlalchemy.url и expected_database_path в одном Config, затем явно вызовем '
           'db_path.open("xb").close(). Режим xb создаёт пустой файл только при свободном имени; '
           'существующий файл не перезаписывается. Только после этого command.upgrade использует сохранённую '
           'историю и защиту env.py, которая требует существующий файл.',
           'Ниже тот же переход повторяется для двух независимых файлов: один получает фактический baseline '
           'ID, второй head. Вывод колонок и проверка именованных CHECK показывают выбранные признаки '
           'структуры; это не полное сравнение схем.',
           'Сохраним полный код как verify_expansion.py в project/. При запуске python -X utf8 '
           'verify_expansion.py введём путь заполненной базы из описи после upgrade. Скрипт проверяет, что '
           'источник существует, а фиксированные имена отдельных копий рядом с ним свободны; исходный резерв '
           'и источник не перезаписываются.',
           'После миграционных проверок отдельная копия используется для отказов старой инициализации и '
           'API/CLI: хеш обнаруживает изменение существующей копии, а отсутствующий путь остаётся '
           'отсутствующим. Проверим коды API и чтение CLI после нового процесса.',
           'В конце Backup API создаёт снимок и восстанавливает его в другой свободный путь. Сравнение '
           'sqlite_schema, table_xinfo, строк и revision подтверждает перечисленные сохранённые объекты и '
           'данные. При ошибке останавливаемся и не объявляем последующие проверки успешными.'],
 'code': 'from pathlib import Path\n'
         'import os\n'
         'import hashlib\n'
         'import sqlite3\n'
         'import subprocess\n'
         'import sys\n'
         'import tempfile\n'
         'from contextlib import closing\n'
         'from alembic import command\n'
         'from alembic.config import Config\n'
         'from alembic.script import ScriptDirectory\n'
         'from sqlalchemy.engine import URL\n'
         '\n'
         'project = Path.cwd().resolve()\n'
         'source = Path(input("Путь заполненной базы после upgrade из описи: ").strip()).resolve()\n'
         'data_dir = source.parent\n'
         'if not source.is_file():\n'
         '    raise FileNotFoundError(source)\n'
         'for name in ("api-check-verified.sqlite3", "init-absent.sqlite3",\n'
         '             "post-upgrade-snapshot-verified.sqlite3", "post-upgrade-restored-verified.sqlite3"):\n'
         '    if (data_dir / name).exists():\n'
         '        raise FileExistsError(data_dir / name)\n'
         '\n'
         'def backup(src, dst):\n'
         '    if dst.exists():\n'
         '        raise FileExistsError(dst)\n'
         '    subprocess.run([sys.executable, "-m", "app.DBase.backup_snapshot", str(src), str(dst)], '
         'cwd=project, check=True)\n'
         '\n'
         '# Каждая проверка схемы получает новый файл и обе опции одного Config.\n'
         'ini = project / "alembic.ini"\n'
         'with tempfile.TemporaryDirectory(dir=data_dir) as temporary:\n'
         '    tmp = Path(temporary)\n'
         '    cfg = Config(str(ini))\n'
         '    cfg.set_main_option("script_location", str(project / "migrations").replace("%", "%%"))\n'
         '    baseline_id = ScriptDirectory.from_config(cfg).get_base()\n'
         '    if baseline_id is None:\n'
         '        raise ValueError("Исходная ревизия не найдена")\n'
         '    print("baseline:", baseline_id)\n'
         '    for filename, revision in [("baseline.sqlite3", baseline_id), ("head.sqlite3", "head")]:\n'
         '        db_path = (tmp / filename).resolve()\n'
         '        db_url = URL.create("sqlite", '
         'database=db_path.as_posix()).render_as_string(hide_password=False)\n'
         '        cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))\n'
         '        cfg.set_main_option("expected_database_path", str(db_path).replace("%", "%%"))\n'
         '        # Только для новой цели после согласования обоих путей.\n'
         '        db_path.open("xb").close()\n'
         '        command.upgrade(cfg, revision)\n'
         '        with closing(sqlite3.connect(db_path)) as db:\n'
         '            columns = [row[1] for row in db.execute("PRAGMA table_info(tasks)")]\n'
         '            ddl = db.execute("SELECT sql FROM sqlite_schema WHERE name = '
         '\'tasks\'").fetchone()[0].lower()\n'
         '        old_columns = ["id", "title", "priority", "is_done", "tags", "title_normalized"]\n'
         '        assert columns == old_columns + ([] if revision == baseline_id else ["description"])\n'
         '        assert "ck_tasks_id_positive" in ddl and "ck_tasks_priority_range" in ddl\n'
         '        print(filename, columns)\n'
         '\n'
         '# Отдельная копия для API и CLI, чтобы их записи не меняли source.\n'
         'api_copy = data_dir / "api-check-verified.sqlite3"\n'
         'backup(source, api_copy)\n'
         'os.environ["PYTHONUTF8"] = "1"\n'
         'os.environ["DATABASE_URL"] = URL.create("sqlite", '
         'database=api_copy.as_posix()).render_as_string(hide_password=False)\n'
         '# Проверяем старую точку входа на существующей и отсутствующей копии.\n'
         'before_hash = hashlib.sha256(api_copy.read_bytes()).hexdigest()\n'
         'refusal = subprocess.run([sys.executable, "-m", "app.DBase.init_db"], cwd=project, '
         'env=os.environ.copy(), capture_output=True, text=True)\n'
         'assert refusal.returncode != 0 and hashlib.sha256(api_copy.read_bytes()).hexdigest() == '
         'before_hash\n'
         'absent = data_dir / "init-absent.sqlite3"\n'
         'if absent.exists():\n'
         '    raise FileExistsError(absent)\n'
         'missing_env = os.environ.copy()\n'
         'missing_env["DATABASE_URL"] = URL.create("sqlite", '
         'database=absent.as_posix()).render_as_string(hide_password=False)\n'
         'direct = subprocess.run([sys.executable, "-c", "from app.DBase.init_db import create_schema; '
         'create_schema()"], cwd=project, env=missing_env, capture_output=True, text=True)\n'
         'assert direct.returncode != 0 and not absent.exists()\n'
         'print("init: оба вызова отказали, данные и отсутствующий путь не изменены")\n'
         'from fastapi.testclient import TestClient\n'
         'from app.api import app\n'
         '\n'
         'with TestClient(app) as client:\n'
         '    before = client.get("/tasks")\n'
         '    assert before.status_code == 200\n'
         '    created = client.post("/tasks", json={"title": "API test", "priority": 2})\n'
         '    assert created.status_code == 201\n'
         '    assert set(created.json()) == {"id", "title", "priority", "is_done"}\n'
         '    created_id = created.json()["id"]\n'
         '    print("API:", before.status_code, created.status_code, sorted(created.json()))\n'
         '\n'
         'cli = subprocess.run([sys.executable, "-m", "app.main"], cwd=project, env=os.environ.copy(), '
         'input="add\\nCLI test\\n3\\nexit\\n", text=True, capture_output=True, check=True)\n'
         'assert "CLI test" in cli.stdout\n'
         'again = subprocess.run([sys.executable, "-m", "app.main"], cwd=project, env=os.environ.copy(), '
         'input="list\\nexit\\n", text=True, capture_output=True, check=True)\n'
         'assert "CLI test" in again.stdout\n'
         'with TestClient(app) as client:\n'
         '    rows = client.get("/tasks").json()\n'
         '    assert any(row["id"] == created_id for row in rows)\n'
         '    assert any(row["title"] == "CLI test" for row in rows)\n'
         '    assert all(set(row) == {"id", "title", "priority", "is_done"} for row in rows)\n'
         'print("CLI: add и чтение после перезапуска прошли")\n'
         '\n'
         '# Два новых разных пути: снимок и его проверочное восстановление.\n'
         'snapshot = data_dir / "post-upgrade-snapshot-verified.sqlite3"\n'
         'restored = data_dir / "post-upgrade-restored-verified.sqlite3"\n'
         'backup(source, snapshot)\n'
         'backup(snapshot, restored)\n'
         'with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(restored)) as copy:\n'
         '    for query in ("PRAGMA table_xinfo(tasks)", "SELECT type, name, tbl_name, sql FROM '
         'sqlite_schema WHERE name NOT LIKE \'sqlite_%\' ORDER BY type, name", "SELECT * FROM tasks ORDER BY '
         'id", "SELECT version_num FROM alembic_version"):\n'
         '        assert original.execute(query).fetchall() == copy.execute(query).fetchall()\n'
         'print("snapshot: объекты sqlite_schema, колонки tasks, строки и revision совпадают")\n',
 'checks': ['Историческая проверка использует конкретный ID исходной ревизии, а проверка head отдельный '
            'файл.',
            'Старая инициализация не создала отсутствующую базу и не изменила существующую.',
            'API и CLI работают с отдельной копией; исходный резерв и заполненная база не изменены.',
            'У снимка и восстановленной копии совпадают объекты sqlite_schema, свойства колонок tasks, '
            'строки и revision.']}}],
           'result': 'Обе учебные базы прошли расширение, прежние данные и интерфейсы проверены, а отдельный '
                     'снимок после расширения восстановлен.'}]}
