"""Проектная практика: исследование отказа на изолированной копии."""

MANUAL_PRACTICE = {85: [{'title': 'Исследуем отказ и восстанавливаем копию',
       'task': 'В изолированной учебной копии Planner есть история схемы и снимок после добавления nullable '
               'description. Работаем из project/, останавливаем API и CLI и не используем реальные данные.',
       'tasks': [{'title': 'Подготовим безопасный вход',
                  'task': 'Перед опытом выясним, какие файлы можно читать и куда разрешено записывать. У нас '
                          'есть исходный резерв до расширения и отдельный проверенный снимок после '
                          'расширения. Остановим API и CLI, назовём абсолютные пути обоих файлов и проверим, '
                          'что они существуют и различаются. В project/data выберем ещё три новых имени для '
                          'опыта, восстановления и повтора; занятое имя не перезаписываем. Откроем снимок '
                          'только для чтения и запишем его схему, revision, число задач и все значения '
                          'полей. У двух синтетических задач одна description равна NULL, другая содержит '
                          'текст. При неизвестном пути или отличии от переданной истории остановимся.',
                  'requirements': ['Исходный резерв и снимок после расширения определены как два '
                                   'существующих разных файла; API и CLI остановлены.',
                                   'Пути data/failure-lab.sqlite3, data/recovered-check.sqlite3 и '
                                   'data/retry-check.sqlite3 свободны и отличаются от источников; при '
                                   'занятости указаны другие новые имена.',
                                   'В описи указаны integrity_check, таблицы, столбцы, CHECK, индексы, '
                                   'внешние ключи, триггеры, revision, число строк и поля id, title, '
                                   'priority, is_done, tags, title_normalized, description по каждому ID.',
                                   'Две синтетические задачи имеют соответственно NULL и непустое '
                                   'description; revision согласована с переданной историей. При неизвестном '
                                   'адресе или несовпадении работа остановлена.'],
                  'result': 'Записаны проверенные адреса, схема, revision и полная опись задач. Свободные '
                            'назначения не созданы.',
                  'hint': 'Вернёмся к четырём ролям файлов и к тому, что проверяет integrity_check.',
                  'answer': {'explanation': 'Опись связывает конкретный снимок с ожидаемой схемой и данными. '
                                            'Одинаковое число строк не доказывает равенство значений.',
                             'steps': ['Проверим текущий каталог и существование каждого источника. Разрешим '
                                       'существующие пути и сравним их с адресами будущих файлов.',
                                       'Откроем снимок только для чтения через Path.resolve().as_uri() с '
                                       'параметром mode=ro и sqlite3.connect с uri=True. Выполним запросы к '
                                       'sqlite_schema, PRAGMA и SELECT всех полей в порядке ID; после чтения '
                                       'закроем соединение.',
                                       'Сравним результаты с переданной описью, отметим revision и оба '
                                       'значения description. При расхождении не продолжим опыт.'],
                             'code': 'PRAGMA integrity_check;\n'
                                     "PRAGMA table_info('tasks');\n"
                                     "PRAGMA index_list('tasks');\n"
                                     "PRAGMA foreign_key_list('tasks');\n"
                                     'SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, '
                                     'name;\n'
                                     'SELECT version_num FROM alembic_version;\n'
                                     'SELECT id, title, priority, is_done, tags, title_normalized, '
                                     'description FROM tasks ORDER BY id;',
                             'checks': ['Целостность возвращает единственное ok, revision совпадает с '
                                        'историей.',
                                        'Для каждой задачи сохранены все семь значений; одно description '
                                        'равно NULL, другое не пустое.',
                                        'При занятой цели или неизвестном источнике мы останавливаемся до '
                                        'записи.']}},
                 {'title': 'Вызовем управляемую ошибку',
                  'task': 'Посмотрим, что остаётся после прерванной миграции, не изменяя снимок. Сначала '
                          'восстановим его в новый data/failure-lab.sqlite3 через app.DBase.backup_snapshot. '
                          'В отдельном failure_lab скопируем alembic.ini и каталог migrations, зададим '
                          'учебный адрес одноразовой базы и контрольный абсолютный путь. Убедимся, что '
                          'env.py сверяет их до подключения и offline configure. Затем создадим отдельную '
                          'ревизию после фактического head: после добавления nullable столбца вызовем '
                          'RuntimeError. Запустим upgrade через конфигурацию failure_lab и сохраним '
                          'сообщение и код завершения.',
                  'requirements': ['Из проверенного снимка создан отдельный data/failure-lab.sqlite3 без '
                                   'перезаписи существующих файлов.',
                                   'В failure_lab находятся отдельные копии alembic.ini и migrations; '
                                   'script_location указывает на скопированную историю, а sqlalchemy.url и '
                                   'абсолютный expected_database_path относятся к одноразовой базе.',
                                   'env.py сверяет оба значения выбранного Config до подключения и offline '
                                   'configure; несовпадающий адрес отклоняется до создания ошибочного файла.',
                                   'В отдельной истории определена revision после фактического head: upgrade '
                                   'добавляет nullable столбец к tasks и вызывает RuntimeError; вывод и '
                                   'ненулевой код команды с --config failure_lab/alembic.ini сохранены.'],
                  'result': 'В одноразовом опыте получен ненулевой код и управляемая ошибка; исходная '
                            'история и оба резерва сохранены.',
                  'hint': 'Сначала проверим, какой ini выбран и какой файл получит соединение. Исключение '
                          'разместим после первого шага учебной ревизии.',
                  'answer': {'explanation': 'Отдельная история не добавляет пробную ревизию в штатный '
                                            'каталог. Пара URL и контрольного пути защищает от случайного '
                                            'подключения к другой базе.',
                             'steps': ['Вызовем модуль снимка с путём проверенного снимка и новым путём '
                                       'одноразовой базы. До этого проверим через Test-Path отсутствие цели.',
                                       'При отсутствии failure_lab создадим каталог командой New-Item '
                                       '-ItemType Directory -Path failure_lab; скопируем ini и migrations '
                                       'командой Copy-Item с -Recurse для каталога.',
                                       'В failure_lab/alembic.ini зададим script_location = '
                                       '%(here)s/migrations, sqlalchemy.url = '
                                       'sqlite:///./data/failure-lab.sqlite3 и expected_database_path как '
                                       'полный путь из Resolve-Path -LiteralPath data/failure-lab.sqlite3. '
                                       'Проверим обе ветви env.py до подключения.',
                                       'Проверим совпадение пути до подключения, создадим revision и '
                                       'проверим её down_revision по реальному head. Не заменяем '
                                       'сгенерированный идентификатор придуманным значением.',
                                       'В сгенерированном upgrade запишем op.add_column для новой nullable '
                                       'sa.Column, а следующей строкой raise RuntimeError. Запустим upgrade '
                                       'с явно выбранным ini и сразу сохраним $LASTEXITCODE и сообщение.'],
                             'code': '.\\.venv\\Scripts\\python.exe -m app.DBase.backup_snapshot '
                                     'data/after-expand-snapshot.sqlite3 data/failure-lab.sqlite3\n'
                                     '.\\.venv\\Scripts\\python.exe -m alembic --config '
                                     'failure_lab/alembic.ini current\n'
                                     '.\\.venv\\Scripts\\python.exe -m alembic --config '
                                     'failure_lab/alembic.ini revision -m "failure probe"\n'
                                     '.\\.venv\\Scripts\\python.exe -m alembic --config '
                                     'failure_lab/alembic.ini upgrade head\n'
                                     '$LASTEXITCODE',
                             'checks': ["Тело upgrade: op.add_column('tasks', sa.Column('failure_note', "
                                        'sa.String(), nullable=True)); следующей строкой raise '
                                        "RuntimeError('учебный отказ'). Импорты: from alembic import op и "
                                        'import sqlalchemy as sa. Сохраняем реальные revision и '
                                        'down_revision.',
                                        'При несовпадении URL и контрольного пути нет подключения и новый '
                                        'ошибочный файл не создан.',
                                        'В ошибочной ветви вывод содержит RuntimeError и ненулевой код; '
                                        'принятую ревизию мы не переписывали.']}},
                 {'title': 'Изучим следы ошибки',
                  'task': 'Команда остановилась с ошибкой. Теперь узнаем, что действительно осталось в '
                          'одноразовой базе: откроем failure-lab.sqlite3 новым соединением только для чтения '
                          'и сравним с описью схему, revision и все строки. Запишем также версии Python, '
                          'SQLite, SQLAlchemy и Alembic. Независимо от того, появился ли новый столбец, '
                          'зафиксируем наблюдение и не будем выполнять stamp или повторную миграцию на этой '
                          'базе.',
                  'requirements': ['Схема и объекты, integrity_check, version_num и семь полей каждой задачи '
                                   'прочитаны из failure-lab.sqlite3 и сопоставлены с описью.',
                                   'Записаны фактические отличия и версии Python, SQLite, SQLAlchemy и '
                                   'Alembic, включая наличие или отсутствие нового столбца.',
                                   'На базе с неизвестным состоянием stamp и повторный upgrade не '
                                   'выполнялись; причина остановки объяснена.'],
                  'result': 'Отдельно записаны ошибка процесса, версия, структура и данные, а обнаруженные '
                            'различия сопоставлены с описью.',
                  'hint': 'Сравним сообщение об ошибке с результатами независимых запросов: они отвечают на '
                          'разные вопросы.',
                  'answer': {'explanation': 'Отметка revision и фактическая схема могут расходиться. Stamp '
                                            'меняет отметку, но не исправляет таблицу. Версии описывают '
                                            'среду этого опыта и сами по себе не гарантируют откат DDL.',
                             'steps': ['Откроем data/failure-lab.sqlite3 новым соединением только для чтения '
                                       'и повторим запросы из первой карточки: integrity_check, table_info, '
                                       'index_list, foreign_key_list, sqlite_schema, version_num и семь '
                                       'полей задач. Закроем соединение.',
                                       'Сопоставим каждую строку по ID, определения объектов и revision с '
                                       'сохранённой описью.',
                                       'В PowerShell из project/ выполним три команды ниже подготовленным '
                                       '.venv: первая покажет версию Python, вторая показывает Alembic, третья показывает '
                                       'сначала версию SQLite, затем SQLAlchemy. Запишем полученный вывод '
                                       'рядом с результатами сравнения.',
                                       'Запишем конкретный результат без предположения о DDL и остановимся '
                                       'до восстановления.'],
                             'code': 'SELECT version_num FROM alembic_version;\n'
                                     "PRAGMA table_info('tasks');\n"
                                     'SELECT id, title, priority, is_done, tags, title_normalized, '
                                     'description FROM tasks ORDER BY id;\n\n'
                                     '# PowerShell из project/:\n'
                                     '.\\.venv\\Scripts\\python.exe --version\n'
                                     '.\\.venv\\Scripts\\python.exe -m alembic --version\n'
                                     '.\\.venv\\Scripts\\python.exe -c "import sqlite3, sqlalchemy; '
                                     'print(sqlite3.sqlite_version, sqlalchemy.__version__)"',
                             'checks': ['Столбец может присутствовать или отсутствовать: записываем '
                                        'наблюдаемое, а не ожидаемое.',
                                        'Записаны фактические версии Python, Alembic, SQLite и SQLAlchemy '
                                        'из трёх команд PowerShell.',
                                        'Отсутствие отличий не объявляем гарантией для других версий '
                                        'SQLite.']}},
                 {'title': 'Восстановим и сравним',
                  'task': 'Вернём проверенный снимок после расширения в новый data/recovered-check.sqlite3. '
                          'Откроем полученный файл и сравним его целостность, схему, revision и каждое '
                          'значение задач с исходной описью. Затем направим обычный CLI через DATABASE_URL '
                          'именно на восстановленный файл и прочитаем существующую задачу новым процессом. '
                          'Если копия не открывается или обнаружено несовпадение, сохраним диагностику и '
                          'остановим передачу.',
                  'requirements': ['Снимок передан как SOURCE, свободный data/recovered-check.sqlite3 как '
                                   'DEST; восстановление не изменило источники.',
                                   'integrity_check, схема, объекты, revision и все поля обеих задач '
                                   'совпадают с описью, включая NULL и непустое description.',
                                   'DATABASE_URL указывает на восстановленный файл; новый процесс app.main '
                                   'показал задачи через list и find и завершён через exit.',
                                   'Ошибка восстановления или несовпадение зафиксированы как остановка '
                                   'передачи.'],
                  'result': 'Отдельная восстановленная база совпадает с описью и читается обычным CLI.',
                  'hint': 'Сначала сравним адреса источника и назначения; затем различим техническую '
                          'целостность и содержимое.',
                  'answer': {'explanation': 'Исходник для backup теперь сам снимок, а назначение является '
                                            'новым файлом. SQL подтверждает внутренние поля, которых нет в '
                                            'выводе CLI.',
                             'steps': ['Проверим существование снимка и отсутствие нового назначения, затем '
                                       'вызовем обёртку с этими путями.',
                                       'Откроем результат в режиме mode=ro, повторим запросы из первой '
                                       'карточки и сопоставим структуру, все объекты, revision и семь полей '
                                       'каждой строки с описью.',
                                       'Зададим явный URL восстановленного файла в отдельном терминале и '
                                       'прочитаем существующую задачу через list и find.'],
                             'code': '.\\.venv\\Scripts\\python.exe -m app.DBase.backup_snapshot '
                                     'data/after-expand-snapshot.sqlite3 data/recovered-check.sqlite3\n'
                                     "$env:DATABASE_URL = 'sqlite:///./data/recovered-check.sqlite3'\n"
                                     '.\\.venv\\Scripts\\python.exe -m app.main',
                             'checks': ['Оба резерва остаются на своих местах; восстановленная база имеет ту '
                                        'же revision и значения.',
                                        'Если файл не открывается или хоть одно поле отличается, '
                                        'восстановление не принимается.']}},
                 {'title': 'Проверим повтор на новой копии',
                  'task': 'Для допустимого повтора возьмём ещё одну новую копию снимка, а не базу с ошибкой. '
                          'Подготовим retry_lab со штатной историей без пробной ревизии, направим обе '
                          'настройки адреса на data/retry-check.sqlite3 и выполним upgrade head. Сравним '
                          'revision, схему и данные с описью. Так мы проверим повтор на текущем head, а не '
                          'переход старой базы от baseline.',
                  'requirements': ['Из снимка получена новая data/retry-check.sqlite3; база с ошибкой не '
                                   'используется.',
                                   'В retry_lab находится копия штатной истории без пробной ревизии; '
                                   'sqlalchemy.url и абсолютный expected_database_path задают адрес '
                                   'retry-check.sqlite3 и проверяются до соединения.',
                                   'upgrade head выполнен с --config retry_lab/alembic.ini; revision, схема '
                                   'и все данные совпадают с описью.'],
                  'result': 'Повтор на head завершён без изменения записей, а диагностическая ошибочная база '
                            'остаётся отдельной.',
                  'hint': 'Сравним каталог ревизий для повтора с каталогом пробной истории.',
                  'answer': {'explanation': 'Повтор на head не применяет повторно уже выполненную ревизию и '
                                            'не заменяет проверку перехода от исходной базы.',
                             'steps': ['Создадим ещё одну копию через обёртку и проверим её адрес.',
                                       'Скопируем штатные ini и migrations в retry_lab, установим пару URL и '
                                       'контрольного пути для новой базы.',
                                       'В retry_lab/alembic.ini зададим script_location = '
                                       '%(here)s/migrations, sqlalchemy.url = '
                                       'sqlite:///./data/retry-check.sqlite3 и expected_database_path из '
                                       'Resolve-Path -LiteralPath data/retry-check.sqlite3. Проверим обе '
                                       'ветви env.py до подключения.',
                                       'Выполним upgrade с --config retry_lab/alembic.ini и сравним '
                                       'revision, схему и все поля с описью.'],
                             'code': '.\\.venv\\Scripts\\python.exe -m app.DBase.backup_snapshot '
                                     'data/after-expand-snapshot.sqlite3 data/retry-check.sqlite3\n'
                                     '.\\.venv\\Scripts\\python.exe -m alembic --config '
                                     'retry_lab/alembic.ini upgrade head',
                             'checks': ['После команды обе строки и значения description совпадают с описью.',
                                        'История retry_lab не содержит пробной ошибочной ревизии.']}}],
       'result': 'Ошибка диагностирована на отдельной базе, а снимок восстановлен и проверен без изменения '
                 'исходных резервов.'}]}
