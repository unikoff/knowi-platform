from typing import Any

MANUAL_PRACTICE: dict[int | str, list[dict[str, Any]]] = {76: [{'title': 'Различаем два отказа SQLite в Planner',
       'task': 'Продолжите работу в изолированной учебной копии SQL Planner. Возьмите исправный DATABASE_URL '
               'и id из своей заметки, повторите штатный GET и запишите id и название. В разделе '
               '«Подготавливаем два учебных отказа» есть команды для двух отдельных адресов и для повторного '
               'запуска. Держите их в той же вкладке PowerShell. В заметке сохраняйте адрес, наблюдаемую '
               'причину и видимый результат каждого прогона.',
       'tasks': [{'title': 'Распознаём по коду, что SQLite не открылась',
                  'task': 'Нужно отличить отказ открытия даже тогда, когда SQLite добавила в код уточнение. '
                          'Вернитесь к разделу «Как прочитать код причины». В примере квитанции извлеките '
                          'мысленно номер и выделите категорию, затем перенесите эти приёмы на исключение из '
                          'готовой обёртки. В app/DBase/session.py рядом с create_session_factory добавьте '
                          'is_sqlite_unavailable(error: DBAPIError) -> bool. Сравнивайте основной код с '
                          'sqlite3.SQLITE_CANTOPEN. Решите, в какой момент убедиться, что код подходит для '
                          'маски, и какую ветку выбрать, если число отсутствует.\n'
                          '\n'
                          'До настройки HTTP проверьте функцию в интерактивном Python из project/. Измените '
                          'число учебной обёртки из теории для 14, 782 и 127, а четвёртую обёртку оставьте '
                          'без поля. Вызовите свою функцию для каждого объекта DBAPIError и запишите пары '
                          'входа и результата.',
                  'requirements': ['В app/DBase/session.py есть is_sqlite_unavailable(error: DBAPIError) -> '
                                   'bool, фабрика Session остаётся доступной.',
                                   'Функция даёт True для 14 и 782. Она даёт False для 127 и при отсутствии '
                                   'sqlite_errorcode.',
                                   'Решение основано на целом коде драйвера и его младшем байте. Имя и текст '
                                   'исключения не участвуют в проверке.'],
                  'result': 'Четыре ручных вызова дают True, True, False, False в указанном порядке.',
                  'hint': 'У квитанций 14 и 782 совпадает категория. Какое промежуточное число должно '
                          'совпасть в вашей функции? Что вернул getattr у обёртки без номера?',
                  'answer': {'explanation': 'У 782 шестнадцатеричная запись 0x030E. Маска оставляет 0x0E, то '
                                            'есть 14. Сравнение всего числа пропустило бы расширенный код. '
                                            'Если номера нет, получаем None и завершаем проверку до маски.',
                             'steps': ['Берём исключение драйвера из error.orig и пытаемся прочитать '
                                       'sqlite_errorcode. Имя поля совпадает с тем, которое прочитали в '
                                       'независимой пробе.',
                                       'Для целого вычисляем основной код и сравниваем его с именованной '
                                       'константой SQLite. Для 127 основной код остаётся 127.',
                                       'Подставляем четыре обёртки из условия. Получаем True, True, False, '
                                       'False. Пока функция только даёт ответ о категории. Решение о '
                                       'пробросе останется на границе ресурса.'],
                             'code': '# app/DBase/session.py. Добавьте импорты и функцию рядом с фабрикой.\n'
                                     'import sqlite3\n'
                                     '\n'
                                     'from sqlalchemy.exc import DBAPIError\n'
                                     '\n'
                                     '\n'
                                     'def is_sqlite_unavailable(error: DBAPIError) -> bool:\n'
                                     '    code = getattr(error.orig, "sqlite_errorcode", None)\n'
                                     '    if not isinstance(code, int):\n'
                                     '        return False\n'
                                     '    primary = code & 0xFF\n'
                                     '    return primary == sqlite3.SQLITE_CANTOPEN'}},
                 {'title': 'Превращаем подтверждённый отказ в безопасный HTTP 503',
                  'task': 'Имея проверенную функцию, подключите её к штатному чтению Planner. В разделе «Где '
                          'причина становится ответом» проследите, почему запрос к файлу происходит после '
                          'выдачи Session. Найдите этот участок в get_db(request) из '
                          'app/core/dependencies.py. Обработайте DBAPIError вокруг работы с выданной '
                          'Session. Выберите, когда поднять существующий StorageError, а когда дать '
                          'исходному исключению пройти дальше. Сохраните закрытие Session. В app/api.py '
                          'зарегистрируйте обработчик по типу StorageError по примеру стойки. Верните '
                          'фиксированный JSON из ТЗ. Перенесите ту же классификацию к sql_service_context в '
                          'app/main.py. В app/cli.py обработайте StorageError вокруг работы одной команды и '
                          'оставьте меню активным. Подходящие учебные адреса и способы наблюдения видны в '
                          'разделе «Подготавливаем два учебных отказа».\n'
                          '\n'
                          'Сначала командой reason проверьте адрес с отсутствующим родителем. Затем включите '
                          'режим http на том же id и проверьте тело. Перед опытом подтвердите чтением JSON, '
                          'что контрольный id и его особое название действительно есть в архиве по прежнему '
                          'адресу этой копии. С тем же отказным DATABASE_URL запустите обычный CLI и введите '
                          'find, контрольный id и exit после возвращения меню.',
                  'requirements': ['Граница получает DBAPIError от реального запроса после выдачи Session. '
                                   'Подтверждённая категория становится StorageError, остальные ошибки '
                                   'повторно поднимаются без подмены причины. Ресурс закрывается на всех '
                                   'путях.',
                                   'Для адреса с отсутствующим родителем локальная диагностика показывает '
                                   'код 14, обычный GET даёт HTTP 503 и JSON с detail «Task storage is '
                                   'temporarily unavailable.»',
                                   'Обычный CLI сообщает «База данных временно недоступна. Команда не '
                                   'выполнена.» и возвращает меню.',
                                   'Архивный маркер читается из подготовленного JSON, но отсутствует в '
                                   'HTTP-ответе и выводе CLI. Пользовательский вывод не содержит пути, SQL, '
                                   'параметров запроса и traceback.'],
                  'result': 'Записаны фактический код 14, безопасный 503 и понятный отказ CLI. Архивный id '
                            'не выдан как результат чтения SQLite.',
                  'hint': 'Сопоставьте момент yield с первым SQL-запросом. Увидит ли исключение обработчик, '
                          'если он охватывает только создание Session?',
                  'answer': {'explanation': 'Engine открывает соединение во время обращения к данным. '
                                            'Короткий блок вокруг создания Session поэтому не ловит отказ '
                                            'этого чтения. Область от выдачи Session до выхода из команды '
                                            'или запроса видит ошибку. При коде 14 она поднимает '
                                            'StorageError, для другой причины выполняет обычный raise.',
                             'steps': ['В одном варианте используем общий контекст available_session в обоих '
                                       'существующих владельцах ресурса. Он сначала выходит из Session, '
                                       'затем решает, следует ли менять тип исключения. Сборку репозитория и '
                                       'session.begin сохраняем, поскольку они обеспечивают прежнюю запись.',
                                       'Подключаем к StorageError готовый HTTP-обработчик. В CLI '
                                       'перехватываем StorageError вокруг уже существующей команды. Для '
                                       'формы из app/cli.py с context_factory внутри execute_command после '
                                       'сообщения возвращаем True, чтобы цикл продолжился. Для формы с '
                                       'context_factory внутри run устанавливаем keep_running = True.',
                                       'Печатаем внутренний код на отдельном вызове репозитория. Свежий GET '
                                       'на выбранном отказном адресе возвращает 503 и фиксированное тело; '
                                       'find того же id сообщает об отказе. Положительная проверка архива '
                                       'подтверждает, что свой маркер мы действительно смогли бы увидеть при '
                                       'подмене источника.'],
                             'code': '# app/DBase/session.py. Добавьте импорты и общий контекст.\n'
                                     'from contextlib import contextmanager\n'
                                     '\n'
                                     'from app.errors import StorageError\n'
                                     '\n'
                                     '\n'
                                     '@contextmanager\n'
                                     'def available_session(session_factory):\n'
                                     '    try:\n'
                                     '        with session_factory() as session:\n'
                                     '            yield session\n'
                                     '    except DBAPIError as error:\n'
                                     '        if is_sqlite_unavailable(error):\n'
                                     '            raise StorageError("Task storage is temporarily '
                                     'unavailable.") from error\n'
                                     '        raise\n'
                                     '\n'
                                     '\n'
                                     '# app/core/dependencies.py. Добавьте импорт available_session.\n'
                                     '# После прежней проверки session_factory в get_db замените ручной '
                                     'try/finally:\n'
                                     'from app.DBase.session import available_session\n'
                                     '\n'
                                     '\n'
                                     'def get_db(request: Request) -> Iterator[Session]:\n'
                                     '    session_factory = getattr(request.app.state, "session_factory", '
                                     'None)\n'
                                     '    if session_factory is None:\n'
                                     '        raise RuntimeError("SQL session factory is not configured")\n'
                                     '    with available_session(session_factory) as session:\n'
                                     '        yield session\n'
                                     '\n'
                                     '\n'
                                     '# app/main.py. Добавьте импорт available_session, замените '
                                     'sql_service_context.\n'
                                     'from app.DBase.session import available_session\n'
                                     '\n'
                                     '\n'
                                     '@contextmanager\n'
                                     'def sql_service_context(session_factory: sessionmaker[Session]) -> '
                                     'Iterator[PlannerService]:\n'
                                     '    with available_session(session_factory) as session:\n'
                                     '        yield build_service(\n'
                                     '            repository=SqlTaskRepository(session),\n'
                                     '            write_scope=session.begin,\n'
                                     '        )\n'
                                     '\n'
                                     '\n'
                                     '# app/api.py. Добавьте импорты и обработчик к существующему app.\n'
                                     'from fastapi import Request\n'
                                     'from fastapi.responses import JSONResponse\n'
                                     '\n'
                                     'from app.errors import StorageError\n'
                                     '\n'
                                     '\n'
                                     '@app.exception_handler(StorageError)\n'
                                     'async def storage_unavailable(_request: Request, _error: '
                                     'StorageError):\n'
                                     '    return JSONResponse(\n'
                                     '        {"detail": "Task storage is temporarily unavailable."},\n'
                                     '        status_code=503,\n'
                                     '    )\n'
                                     '\n'
                                     '\n'
                                     '# app/cli.py. Добавьте импорт StorageError; блок ниже стоит внутри '
                                     'run,\n'
                                     '# в ветке распознанной команды. Сохраните исходную execute_command.\n'
                                     '# Если контекст уже открывается в execute_command, оберните его там\n'
                                     '# и после фиксированного сообщения продолжите обычный цикл.\n'
                                     '# try:\n'
                                     '#     with context_factory() as command_service:\n'
                                     '#         keep_running = execute_command(command_service, command, '
                                     'read, write)\n'
                                     '# except StorageError:\n'
                                     '#     write("База данных временно недоступна. Команда не выполнена.")\n'
                                     '#     keep_running = True'}},
                 {'title': 'Сохраняем внутреннюю причину и повторяем здоровый запуск',
                  'task': 'В разделе «Где причина становится ответом» посмотрите, как читаемый файл без '
                          'таблицы отличался от закрытой стойки. По командам в разделе «Подготавливаем два '
                          'учебных отказа» возьмите созданный without-tasks.sqlite3 и повторите тот же GET, '
                          'который на недоступном адресе дал 503. Сопоставьте публичный результат с '
                          'отдельным чтением причины. Запишите, по какому наблюдению вы поняли, что файл '
                          'открылся. Верните исправный адрес и запустите новый GET известной задачи.\n'
                          '\n'
                          'Теперь сохраните повторяемую проверку общего адреса. В разделе «Как два запуска '
                          'подтверждают общее хранилище» второй читатель видит именно свежую отметку. В '
                          'project/tests/test_storage_availability.py добавьте '
                          'test_smoke_api_and_cli_read_the_same_task. Подготовьте одну SQLite в tmp_path '
                          'через штатный init_db. Передайте один временный DATABASE_URL процессу HTTP до '
                          'импорта app.api и обычному CLI. Создайте задачу с новым названием через штатный '
                          'POST /tasks и возьмите выданный id. Прочитайте эту задачу штатным GET и через '
                          'обычный CLI find в отдельном процессе на том же DATABASE_URL. Решите, какие '
                          'наблюдения сравнить с исходной новой записью. Прежние тесты в файле сохраняются.',
                  'requirements': ['Файл without-tasks.sqlite3 открывается и даёт HTTP 500 с телом «Internal '
                                   'Server Error». Локальная причина · код 1 и no such table: tasks. В '
                                   'публичном результате отсутствуют путь, SQL, параметры и traceback.',
                                   'Свежий GET после возврата исправного адреса даёт 200 и прежние id и '
                                   'название. Временная настройка терминала восстановлена.',
                                   'Тест создаёт запись через настоящий POST с ответом 201. Настоящий GET с '
                                   'выданным id даёт 200, а GET и CLI подтверждают id и название этой записи '
                                   'на одном временном SQLite-адресе.',
                                   'HTTP-проба закрывает TestClient. Процессы init, HTTP и CLI получают '
                                   'временный URL через своё окружение; прежние настройки родительского '
                                   'запуска сохраняются.',
                                   'Из project/ команда .venv/Scripts/python.exe -m pytest -q '
                                   'tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task '
                                   'проходит.'],
                  'result': 'Зафиксированы безопасный 500, новый успешный GET и один проходящий тест общего '
                            'чтения. Сохранены исправный URL и контрольный id для проверки сбоя записи.',
                  'hint': 'Какой id обязан появиться в обоих чтениях, и откуда его можно взять? Что '
                          'напечатает CLI, если передать ему другой SQLite-файл с такой же схемой?',
                  'answer': {'explanation': 'HTTP 500 вместе с локальным кодом 1 и именем отсутствующей '
                                            'таблицы показывает, что соединение открылось. Потом тест '
                                            'создаёт уникальную задачу через один штатный вход. У GET и CLI '
                                            'нет другого источника этой задачи. Если CLI направить в '
                                            'отдельную пустую SQLite, GET останется успешным, а ожидаемая '
                                            'строка CLI исчезнет.',
                             'steps': ['На without-tasks.sqlite3 запускаем http и reason. Получаем 500, '
                                       'затем 1 и no such table: tasks. Восстанавливаем $workingUrl, '
                                       'получаем 200 на первоначальном id в новом процессе и возвращаем '
                                       '$beforeUrl.',
                                       'Задаём временный DATABASE_URL только в окружении дочерних процессов. '
                                       'Запускаем обычный init_db на этом адресе. Затем в новом Python '
                                       'импортируем app, открываем TestClient, выполняем POST, берём id из '
                                       'его JSON и проверяем GET. Если Session создаётся на startup, она уже '
                                       'увидит нужный URL.',
                                       'Прочитанный id возвращаем в pytest в JSON. После завершения процесса '
                                       'TestClient запускаем обычный app.main с тем же окружением; в его '
                                       'stdin отправляем find, выданный id и exit. Сравниваем id и название '
                                       'в одной строке stdout.',
                                       'Из project/ запускаем .venv/Scripts/python.exe -m pytest -q '
                                       'tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task. '
                                       'Ожидаем 1 passed.'],
                             'code': '# project/tests/test_storage_availability.py\n'
                                     'import json\n'
                                     'import os\n'
                                     'import subprocess\n'
                                     'import sys\n'
                                     '\n'
                                     '\n'
                                     'READ_TASK = """\n'
                                     'import json\n'
                                     'from fastapi.testclient import TestClient\n'
                                     'from app.api import app\n'
                                     '\n'
                                     'with TestClient(app) as client:\n'
                                     '    title = "Учебная проверка фонаря"\n'
                                     '    created = client.post("/tasks", json={"title": title, "priority": '
                                     '4})\n'
                                     '    assert created.status_code == 201\n'
                                     '    task_id = created.json()["id"]\n'
                                     '    found = client.get(f"/tasks/{task_id}")\n'
                                     '    assert found.status_code == 200\n'
                                     '    task = found.json()\n'
                                     '    assert task["id"] == task_id\n'
                                     '    assert task["title"] == title\n'
                                     '    print(json.dumps(task))\n'
                                     '"""\n'
                                     '\n'
                                     '\n'
                                     'def test_smoke_api_and_cli_read_the_same_task(tmp_path):\n'
                                     '    database = tmp_path / "shared.sqlite3"\n'
                                     '    database_url = f"sqlite:///{database.as_posix()}"\n'
                                     '    env = {**os.environ, "DATABASE_URL": database_url, '
                                     '"PYTHONIOENCODING": "utf-8"}\n'
                                     '    options = dict(env=env, capture_output=True, encoding="utf-8", '
                                     'check=True)\n'
                                     '\n'
                                     '    subprocess.run([sys.executable, "-m", "app.DBase.init_db"], '
                                     '**options)\n'
                                     '    http = subprocess.run([sys.executable, "-c", READ_TASK], '
                                     '**options)\n'
                                     '    task = json.loads(http.stdout.splitlines()[-1])\n'
                                     '    task_id, title = task["id"], task["title"]\n'
                                     '\n'
                                     '    cli = subprocess.run(\n'
                                     '        [sys.executable, "-m", "app.main"],\n'
                                     '        input=f"find\\n{task_id}\\nexit\\n",\n'
                                     '        **options,\n'
                                     '    )\n'
                                     '    assert f"[{task_id}] {title} |" in cli.stdout'}}],
       'result': 'В проекте различаются два реальных отказа SQLite, штатные API и CLI возвращаются к общему '
                 'хранилищу, а проверка общего чтения повторяется отдельной командой pytest.'}],
 '76.1': [{'title': 'Что сохранилось после сбоя записи',
           'task': 'Продолжим работу со своим SQL Planner в `project/`. В разделе «Проверяем, что работа '
                   'продолжилась» возьмём короткие опоры для `tests/test_commit_boundary.py`: адрес, '
                   'схему, счётчик закрытий и сборку сервиса. Каждая тестовая функция получает свой '
                   '`tmp_path`. В собственной подготовке откроем временную SQLite. В `with '
                   'sessions.begin() as seed` добавим две `TaskRow`. У 120: «Осмотр», приоритет 2, `True`, '
                   "метки `['склад']`. У 240: «Осмотр», приоритет 4, `False`, метки `['выезд']`. У обеих "
                   "`title_normalized='осмотр'`. В первом снимке проверим, что обе записи видны. У каждой "
                   'проверки свой временный файл. После работы освобождаем Engine.',
           'tasks': [{'title': 'Доказываем откат по полному снимку',
                      'task': 'В разделе «Где прерывается запись» посмотрим, почему номер уже появился '
                              'после flush, а выдача пропала из журнала после отката. В разделе «Что '
                              'сравнивает независимый читатель» увидим, почему число строк может совпадать '
                              'даже после их изменения.\n'
                              '\n'
                              'В `tests/test_commit_boundary.py` напишем '
                              '`test_rollback_then_new_write(tmp_path)`. Сначала сохраним исходный снимок '
                              'с обеими задачами. Затем попросим Planner заменить задачу 120: новый '
                              'заголовок «Замена фильтра», приоритет 5, статус `False`. В разделе '
                              '«Проверяем, что работа продолжилась» найдём способ передать область записи '
                              'существующему сервису. Сразу после `yield` внутри `begin()` вызовем '
                              '`writer.flush()` и прочитаем новый заголовок. Затем в той же области '
                              'поднимем `ControlledWriteError`. Ожидаемую ошибку отметим через '
                              '`pytest.raises`, как в разделе «Как увидеть точку сбоя». После выхода '
                              'сравним сохранённые записи с исходным снимком. Нам важно увидеть и ту '
                              'задачу, которую пытались заменить, и её соседа.\n'
                              '\n'
                              'Из `project/` запустим `./.venv/Scripts/python.exe -m pytest -q -s '
                              'tests/test_commit_boundary.py::test_rollback_then_new_write`.',
                      'requirements': ['На своём `tmp_path` создана SQLite. Исходный снимок с полной '
                                       'информацией о 120 и 240 снят до замены.',
                                       'Замена передаёт id 120, `title="Замена фильтра"`, `priority=5`, '
                                       '`is_done=False`. После `yield` в открытой транзакции выполняется '
                                       '`writer.flush()`. Контрольное чтение без autoflush показывает '
                                       'новый заголовок. Учебный сбой прерывает ту же транзакцию сразу '
                                       'после этого чтения. `pytest.raises(ControlledWriteError)` '
                                       'охватывает только попытку записи.',
                                       'Новый снимок снят после закрытия пишущей Session. Утверждение '
                                       'проверяет состав ключей и все шесть полей каждой исходной строки.'],
                      'result': 'Тест проходит. Журнал показывает `flush, title=Замена фильтра`, затем '
                                '`close=1` у писателя. Другая Session читает исходные записи 120 и 240 без '
                                'изменений.',
                      'hint': 'В журналах выдачи из теории число строк осталось прежним, хотя один предмет '
                              'сменился. Вспомним, какое сравнение обнаружило это расхождение.',
                      'answer': {'explanation': 'Явный вызов `writer.flush()` передал изменения SQLite '
                                                'внутри открытой транзакции. Новый заголовок уже виден '
                                                'этой Session. Ошибка вышла из `begin()` до подтверждения. '
                                                'Поэтому свежий снимок должен совпасть с исходным по '
                                                'значениям обеих строк.',
                                 'steps': ['Готовим файл через `tmp_path` и сохраняем две строки. В своей '
                                           'функции снимка открываем новую Session, забираем поля '
                                           '`TaskRow` в кортежи и раскладываем их по id. Сохраняем '
                                           '`before` до попытки.',
                                           'Открываем `with sessions() as writer` внутри '
                                           '`pytest.raises(ControlledWriteError)`. Передаём сервису '
                                           'репозиторий на writer и свой `scope` с `@contextmanager`. '
                                           'Сразу после `yield` вызываем `writer.flush()` на той же '
                                           'Session. Затем читаем заголовок без autoflush. После '
                                           'подтверждения нового значения пишем его в журнал и поднимаем '
                                           'ошибку ещё внутри `begin()`.',
                                           'После выхода и закрытия писателя читаем снова. У 120 вернулся '
                                           '«Осмотр» с приоритетом 2, статусом `True`, меткой «склад» и '
                                           'прежней поисковой копией. У 240 прежние поля. Равный снимок '
                                           'отсекает случай, когда число строк осталось прежним, но '
                                           'обновление ошибочно сохранилось.'],
                                 'code': '# Внутри пишущей Session. Передаём scope как write_scope.\n'
                                         '@contextmanager\n'
                                         'def scope():\n'
                                         '    with writer.begin():\n'
                                         '        yield\n'
                                         '        writer.flush()\n'
                                         '        with writer.no_autoflush:\n'
                                         '            seen_title = '
                                         'writer.scalar(select(TaskRow.title).where(TaskRow.id == 120))\n'
                                         '        assert seen_title == "Замена фильтра"\n'
                                         '        print("flush:", seen_title)\n'
                                         '        raise ControlledWriteError("Учебный сбой записи.")\n'
                                         '\n'
                                         '# После выхода из контекстов записи и обработки ошибки.\n'
                                         'rolled_back = snapshot(sessions)\n'
                                         'assert rolled_back == before',
                                 'checks': ['Из `project/`: `./.venv/Scripts/python.exe -m pytest -q -s '
                                            'tests/test_commit_boundary.py::test_rollback_then_new_write`.',
                                            'В выводе видим отметку flush, одно закрытие пишущей Session и '
                                            'другое число в метке читателя. Если пропустить '
                                            '`writer.flush()`, в сборке, где замена ждёт commit, чтение '
                                            'без autoflush вернёт «Осмотр». Это проверка подготовки '
                                            'опыта.']}},
                     {'title': 'Подтверждаем запись при сбое после commit',
                      'task': 'В разделе «Где прерывается запись» посмотрим, что увидел читатель после '
                              'ошибки при выдаче «Стойки». Теперь повторим ту же замену задачи 120, но '
                              'после успешного выхода из области записи поднимем ошибку.\n'
                              '\n'
                              'В том же файле напишем отдельную функцию '
                              '`test_write_stays_committed(tmp_path)`. Для неё подготовим новый файл с '
                              'первоначальными задачами. Сохраним исходный снимок, затем передадим при '
                              'замене задачи 120 те же «Замена фильтра», 5 и `False`. Сохраним '
                              '`writer.flush()` сразу после `yield` и контрольное чтение внутри `begin()`. '
                              'На этот раз перенесём `raise` за выход из `begin()`. После выхода отметим '
                              'commit в журнале. После ожидаемой ошибки снимем состояние новой Session. '
                              'Сравним именно ту запись, которую меняли, и убедимся, что сосед остался '
                              'прежним.\n'
                              '\n'
                              'Из `project/` запустим `./.venv/Scripts/python.exe -m pytest -q -s '
                              'tests/test_commit_boundary.py::test_write_stays_committed`.',
                      'requirements': ['В отдельном тесте исходное состояние читается до попытки. Тот же '
                                       'вызов замены выполняется внутри '
                                       '`pytest.raises(ControlledWriteError)`. Явный `writer.flush()` и '
                                       'чтение без autoflush находятся после `yield` внутри `begin()`. '
                                       'Отметка commit и тот же сбой идут сразу за его успешным выходом.',
                                       'После закрытия писателя новый снимок содержит ровно id 120 и 240. '
                                       'У 120 новые заголовок, приоритет и статус, новый '
                                       '`title_normalized` и прежние id и tags.',
                                       'Все поля 240 совпадают с исходным снимком.'],
                      'result': 'Тест проходит. После flush в журнале появляется commit, затем закрытие '
                                'писателя. Свежий читатель видит новую версию задачи 120 и исходную 240.',
                      'hint': 'Разложим ожидаемую запись по полям. Часть значений пришла в команду, часть '
                              'сохранится из старой строки, а поисковая копия зависит от нового заголовка.',
                      'answer': {'explanation': 'Внутри области `writer.flush()` отправляет UPDATE, затем '
                                                'нормальный выход из `begin()` подтверждает его. Тот же '
                                                'сбой происходит уже после подтверждения. Независимое '
                                                'чтение проверяет конкретную версию цели и показывает, что '
                                                'старая версия соседа сохранилась.',
                                 'steps': ['Читаем отдельный исходный снимок `before`. Повторяем тот же '
                                           'вызов замены. Оставляем `writer.flush()` и чтение без '
                                           'autoflush после `yield` внутри `begin()`. На этой точке видим '
                                           '«Замена фильтра» в пишущей Session. Отметку commit и `raise` '
                                           'переносим за блок `begin()`. Ошибка сохраняет прежний класс и '
                                           'сообщение.',
                                           'Готовим ожидаемое состояние по id. Из исходного снимка берём '
                                           'все строки, в ожидаемой версии 120 меняем три поля и поисковый '
                                           'заголовок. Метка «склад» остаётся у задачи 120.',
                                           'Свежий снимок сравниваем с ожидаемым целиком. Если проверить '
                                           'только видимый `title`, можно пропустить прежнее значение '
                                           '`title_normalized`. Если сравнить с исходным снимком, проверка '
                                           'справедливо упадёт: подтверждённая строка изменилась.'],
                                 'code': '# Внутри пишущей Session. Передаём scope как write_scope.\n'
                                         '@contextmanager\n'
                                         'def scope():\n'
                                         '    with writer.begin():\n'
                                         '        yield\n'
                                         '        writer.flush()\n'
                                         '        with writer.no_autoflush:\n'
                                         '            seen_title = '
                                         'writer.scalar(select(TaskRow.title).where(TaskRow.id == 120))\n'
                                         '        assert seen_title == "Замена фильтра"\n'
                                         '        print("flush:", seen_title)\n'
                                         '    print("commit")\n'
                                         '    raise ControlledWriteError("Учебный сбой записи.")\n'
                                         '\n'
                                         '# После выхода из блока с ожидаемым исключением\n'
                                         'expected = before.copy()\n'
                                         'expected[120] = (120, "Замена фильтра", 5, False, '
                                         'before[120][4], "замена фильтра")\n'
                                         'assert snapshot(sessions) == expected',
                                 'checks': ['Из `project/`: `./.venv/Scripts/python.exe -m pytest -q -s '
                                            'tests/test_commit_boundary.py::test_write_stays_committed`.',
                                            'Свежий снимок показывает «Замена фильтра» у 120 и «Осмотр» у '
                                            '240. В поисковой копии задачи 120 записано «замена '
                                            'фильтра».']}},
                     {'title': 'Проверяем новую успешную операцию',
                      'task': 'В разделе «Проверяем, что работа продолжилась» разберём, как номер новой '
                              'квитанции приводит к нужной строке журнала. Вернёмся к функции '
                              '`test_rollback_then_new_write`. На этом шаге её первая часть уже '
                              'подтвердила откат и оставила в том же временном файле две исходные задачи.\n'
                              '\n'
                              'После проверки отката откроем новый пишущий ресурс с обычным '
                              '`writer.begin`. Создадим «Калибровку» с приоритетом 3, без меток. Сохраним '
                              'id, который вернул Planner. После закрытия этого писателя откроем ещё одну '
                              'Session через свою функцию снимка и проверим, появилась ли именно эта '
                              'задача. Сверим её значения и убедимся, что две исходные строки '
                              'сохранились.\n'
                              '\n'
                              'Из `project/` снова запустим `./.venv/Scripts/python.exe -m pytest -q -s '
                              'tests/test_commit_boundary.py::test_rollback_then_new_write`.',
                      'requirements': ['Новая запись выполняется после пройденной проверки отката, внутри '
                                       'первой тестовой функции и на её временном SQLite-файле.',
                                       'Для `add_task` открыта новая Session. Сервис получает обычный '
                                       '`writer.begin`. id новой задачи берётся из результата вызова.',
                                       'После закрытия штатного писателя новый снимок содержит ровно одну '
                                       'дополнительную запись по полученному id. У неё заголовок '
                                       '«Калибровка», приоритет 3, статус `False`, пустые tags и '
                                       '`title_normalized` со значением «калибровка». Две исходные строки '
                                       'сохраняют все поля.'],
                      'result': 'Расширенный тест проходит. Журнал показывает разные Session для отказа, '
                                'штатной записи и нового чтения. По фактическому id читается «Калибровка», '
                                'а исходные задачи остаются в базе.',
                      'hint': 'Вспомним, где в примере появился номер квитанции. Для результата записи '
                              'найдём этот же номер уже у другого читателя.',
                      'answer': {'explanation': 'Штатная запись проходит уже после отката. Ответ сервиса '
                                                'даёт номер, по которому следующий независимый читатель '
                                                'находит строку. Так мы проверяем и работоспособность '
                                                'новой операции, и её сохранение.',
                                 'steps': ['После сравнения `rolled_back` и `before` снова открываем `with '
                                           'sessions() as writer`. Собираем Planner с репозиторием на '
                                           'writer и `write_scope=writer.begin`. Вызываем `add_task` для '
                                           '«Калибровка» и 3, сохраняем результат как `created`.',
                                           'После закрытия писателя берём `created.id`. Его ещё нет в '
                                           'исходном снимке, поэтому он добавляет ровно один новый ключ.',
                                           'Собираем ожидаемое состояние поверх снимка после отката и '
                                           'читаем все строки через новую Session. Если взять заранее '
                                           'придуманный id или ограничиться проверкой самого объекта '
                                           '`created`, сохранение строки на диске останется без проверки.'],
                                 'code': '# После выхода из контекста штатной записи\n'
                                         'new_id = created.id\n'
                                         'assert new_id not in rolled_back\n'
                                         'expected = rolled_back.copy()\n'
                                         'expected[new_id] = (new_id, "Калибровка", 3, False, (), '
                                         '"калибровка")\n'
                                         'assert snapshot(sessions) == expected',
                                 'checks': ['Из `project/`: `./.venv/Scripts/python.exe -m pytest -q -s '
                                            'tests/test_commit_boundary.py::test_rollback_then_new_write`.',
                                            'В журнале видно по одному закрытию для каждого из двух '
                                            'пишущих ресурсов и отдельную метку итогового чтения. В новом '
                                            'снимке три строки.']}}],
           'result': 'В `tests/test_commit_boundary.py` проверены полный откат, сохранённая замена после '
                     'позднего сбоя и новая успешная запись после отката. Каждый вывод подтверждён '
                     'независимым снимком.'}],
 77: [{'title': 'Наблюдаем одну выборку Planner',
       'task': 'Продолжите свой SQL Planner. Настроим отдельную учебную SQLite, запустим обычный API в '
               'TestClient и соберём ответ одного GET, его SQL и пояснение плана по карточкам в редакторе '
               'проекта.',
       'tasks': [{'title': 'Поймаем один вызов',
                  'task': 'Оператор просит одну ещё открытую заявку с наименьшим номером. Выберите вызов '
                          'обычного API Planner и его параметры. `/health` сообщает состояние API, '
                          '`/stats` выдаёт счётчики, `/tasks` выдаёт список. В списке `is_done` задаёт '
                          'статус: `false` для открытых, `true` для завершённых. При пропуске параметра '
                          'видны оба статуса. `sort_desc` переключает порядок id: `false` для возрастания, '
                          '`true` для убывания. `limit` задаёт размер выдачи.\n'
                          '\n'
                          'Из `project/` в отдельном PowerShell задайте `DATABASE_URL` новой временной '
                          'SQLite до первого запуска Python и импорта `app.api`. Создайте схему и '
                          'запустите свой сценарий. `Join-Path` соединяет временную папку и имя, '
                          '`NewGuid()` добавляет уникальную часть, `Replace` приводит разделители пути к '
                          'форме SQLite URL. Повторяйте этот блок целиком после правок, чтобы новый прогон '
                          'начался с пустой таблицы.\n'
                          '\n'
                          '```powershell\n'
                          '$probeDb = Join-Path ([IO.Path]::GetTempPath()) ("planner-observe-" + '
                          '[guid]::NewGuid().ToString("N") + ".sqlite3")\n'
                          '$env:DATABASE_URL = "sqlite:///" + $probeDb.Replace(\'\\\', \'/\')\n'
                          './.venv/Scripts/python.exe -m app.DBase.init_db\n'
                          './.venv/Scripts/python.exe -m observe_sql\n'
                          '```\n'
                          '\n'
                          'Вернитесь к разделу «Окно наблюдения за одним действием». В '
                          '`app/DBase/sql_trace.py` перенесите нейтральный `capture_sql(engine)` из '
                          'примера каталога и оставьте в нём только захват и чтение плана. В '
                          '`observe_sql.py` импортируйте `app` из `app.api` и `TestClient` из '
                          '`fastapi.testclient`. Вход в `with TestClient(app) as client` запускает '
                          'приложение. Уже внутри этого блока прочитайте фактическую '
                          '`app.state.session_factory` и получите Engine через `get_bind()` короткой '
                          'Session этой фабрики. Установите `engine.hide_parameters = True`.\n'
                          '\n'
                          'Оставаясь в этом же клиенте, до окна захвата откройте `factory.begin()`: по '
                          'выходе весь набор будет записан. Через `session.add(...)` добавьте четыре '
                          '`TaskRow` из `app.DBase.models`: id 4 завершён, 9 открыт, 14 открыт, 21 '
                          'завершён. Заголовки `demo 4`, `demo 9`, `demo 14`, `demo 21`, у всех '
                          '`priority=2`, `tags=[]`, `title_normalized` совпадает с заголовком. Повторите '
                          'пробу с маркером из теории в отдельном окне на этом же Engine.\n'
                          '\n'
                          'Теперь выберите момент подписки для одного GET. `client.get(path, params=...)` '
                          'передаёт параметры из словаря, а `response.json()` возвращает разобранный '
                          'ответ. Сохраните `response` и `trace` для следующих карточек, выведите '
                          '`response.status_code`, номера из ответа и формы команд из `.statement`. '
                          'Повторите чтение после снятия подписки, внутри того же клиента.',
                  'requirements': ['Учебный `DATABASE_URL` задан до импорта `app.api`. После входа в `with '
                                   'TestClient(app)` Engine получен через Session фактической фабрики API. '
                                   'Связанные значения скрыты через `hide_parameters=True`, `echo` '
                                   'выключен.',
                                   'Схема подготовлена до запуска клиента, а набор и отдельная проба с '
                                   'маркером выполнены внутри клиента до целевого окна. В пробе виден '
                                   '`SELECT ?`, значение маркера отсутствует в представлении следа, после '
                                   'повторного чтения размер списка тот же.',
                                   'В целевом окне выполнен один GET. В выводе есть его HTTP-статус, один '
                                   'нужный id и один SELECT выдачи таблицы `tasks`. Служебные команды, '
                                   'если есть, отделены от него.',
                                   'После окна тот же GET через ещё открытый клиент даёт тот же ответ, а '
                                   '`trace` сохраняет прежний размер. Обычный запуск Planner не включает '
                                   'подписку.'],
                  'result': 'В `observe_sql.py` есть `response` и `trace`: один ответ обычного API и след '
                            'его чтения. Повтор вне окна вернул тот же ответ и оставил след прежним.',
                  'hint': 'Если фабрика ещё недоступна, проверьте, что строка её чтения находится внутри '
                          '`with TestClient(app)`. При пустом следе сверяйте Engine и место GET '
                          'относительно окна. Если в следе видна подготовка набора, окно открыто слишком '
                          'рано.',
                  'answer': {'explanation': 'После запуска приложения короткая Session штатной фабрики '
                                            'возвращает Engine, который обслужит GET. Для запроса сперва '
                                            'отбираем открытые заявки. На этой базе остаются 9 и 14, а при '
                                            'порядке по возрастанию первой будет 9.',
                             'steps': ['В отдельном PowerShell задайте `DATABASE_URL` новой временной '
                                       'SQLite до импорта `app.api` и выполните init. В `observe_sql.py` '
                                       'войдите в `with TestClient(app) as client`, чтобы отработал '
                                       'запуск. Если фабрика создаётся при запуске, обращение к ней до '
                                       'входа в `with` даёт `AttributeError`.',
                                       'Внутри клиента прочитайте `app.state.session_factory`, откройте её '
                                       'короткую Session и получите Engine через `session.get_bind()`. '
                                       'Установите `engine.hide_parameters = True`. Затем запишите набор '
                                       'через `factory.begin()` и выполните отдельную пробу с маркером. '
                                       'Она выведет `SELECT ?`, `False`, `1 1`.',
                                       'В том же клиенте откройте новое окно и вызовите `GET /tasks` с '
                                       '`is_done=false`, `sort_desc=false`, `limit=1`. Запрос вернёт `200` '
                                       'и id `[9]`. При выходе `finally` снимает функцию-наблюдатель.',
                                       'Повторите тот же GET после закрытия окна, пока клиент открыт, и '
                                       'сравните ответ и длину списка. Если подписаться на новый Engine с '
                                       'тем же URL, ответ останется `[9]`, а список того наблюдателя будет '
                                       'пустым.'],
                             'code': '# observe_sql.py. Сначала задайте учебный DATABASE_URL и выполните '
                                     'init.\n'
                                     'from fastapi.testclient import TestClient\n'
                                     '\n'
                                     'from app.api import app\n'
                                     'from app.DBase.models import TaskRow\n'
                                     'from app.DBase.sql_trace import capture_sql\n'
                                     '\n'
                                     'with TestClient(app) as client:\n'
                                     '    factory = app.state.session_factory\n'
                                     '    with factory() as session:\n'
                                     '        engine = session.get_bind()\n'
                                     '    engine.hide_parameters = True\n'
                                     '\n'
                                     '    with factory.begin() as session:\n'
                                     '        for task_id, done in [(4, True), (9, False), (14, False), '
                                     '(21, True)]:\n'
                                     '            title = f"demo {task_id}"\n'
                                     '            session.add(TaskRow(\n'
                                     '                id=task_id, title=title, priority=2, is_done=done,\n'
                                     '                tags=[], title_normalized=title,\n'
                                     '            ))\n'
                                     '\n'
                                     '    marker = "catalog-marker"\n'
                                     '    with capture_sql(engine) as probe:\n'
                                     '        with engine.connect() as connection:\n'
                                     '            connection.exec_driver_sql("SELECT ?", '
                                     '(marker,)).scalar_one()\n'
                                     '\n'
                                     '    probe_size = len(probe)\n'
                                     '    with engine.connect() as connection:\n'
                                     '        connection.exec_driver_sql("SELECT ?", '
                                     '(marker,)).scalar_one()\n'
                                     '\n'
                                     '    print(probe[0].statement)\n'
                                     '    print(marker in repr(probe))\n'
                                     '    print(probe_size, len(probe))\n'
                                     '\n'
                                     '    params = {"is_done": "false", "sort_desc": "false", "limit": 1}\n'
                                     '    with capture_sql(engine) as trace:\n'
                                     '        response = client.get("/tasks", params=params)\n'
                                     '\n'
                                     '    ids = [item["id"] for item in response.json()]\n'
                                     '    print(response.status_code, ids)\n'
                                     '    for entry in trace:\n'
                                     '        print(entry.statement)\n'
                                     '\n'
                                     '    before = len(trace)\n'
                                     '    repeated = client.get("/tasks", params=params)\n'
                                     '    print(repeated.json() == response.json(), before, len(trace))',
                             'checks': ['Фабрика доступна внутри `with TestClient(app)`. Проба с маркером '
                                        'даёт `SELECT ?`, `False`, `1 1`. Штатный GET даёт `200 [9]`, а '
                                        'повтор вне окна оставляет длину следа прежней.',
                                        'GET `/health` и подписка на отдельный Engine с тем же URL '
                                        'оставляют список пустым. Ответ `200` сам по себе ещё не '
                                        'подтверждает захват.']}},
                 {'title': 'Выберем доказательство по следу',
                  'task': 'Теперь используйте `trace` из своей первой карточки. Нужно выбрать запись, '
                          'которая вернула выдачу, и выяснить, где ушли лишние заявки. Сопоставьте её с '
                          'двумя контрольными формами ниже. Оба варианта дали оператору тот же id. '
                          'Дополнительный шаг приложения до сборки ответа в них мог отличаться.\n'
                          '\n'
                          '```sql\n'
                          'A  SELECT id, is_done FROM tasks ORDER BY id ASC\n'
                          'B  SELECT id, is_done FROM tasks WHERE is_done = ? ORDER BY id ASC LIMIT ?\n'
                          '```\n'
                          '\n'
                          'В разделе «Где выполнен отбор» посмотрите, как различаются промежуточные списки '
                          'из SQLite и одинаковый внешний результат. В своей выдаче ищите именно `SELECT` '
                          'по `tasks`. Сравните смысл `WHERE`, `ORDER BY` и `LIMIT`, даже если колонки, '
                          'регистр или заполнители записаны иначе. Сохраните выбранную запись как '
                          '`selection` рядом с короткой заметкой о своём наблюдении. Добавьте этот шаг '
                          'внутри прежнего `with TestClient`, после закрытия окна захвата.',
                  'requirements': ['Из `trace` однозначно выбрана одна относящаяся к GET запись. Она '
                                   'сохранена как `selection`; служебные операции исключены.',
                                   'В заметке указано, какую форму поддерживает фактический SQL. Приведён '
                                   'фрагмент, который показывает место отбора по статусу, и отмечены смысл '
                                   'порядка и предела.',
                                   'Объяснено, как один внешний список получается при обеих формах. Ответ '
                                   'опирается на промежуточный набор, который увидит Python.'],
                  'result': 'В сценарии сохранена `selection`, а место отбора обосновано конкретной '
                            'командой из собственного GET.',
                  'hint': 'Отправьте мысленно обе SQL-команды к таблице. Когда в одной из них нет условия '
                          'по статусу, какие номера увидит приложение?',
                  'answer': {'explanation': 'В форме A `ORDER BY` упорядочивает все четыре строки. '
                                            'Приложение может отфильтровать их и взять первую открытую. В '
                                            'наблюдаемом Planner эту работу передали в SQL, что видно по '
                                            'предикату статуса и пределу.',
                             'steps': ['Внутри `with TestClient`, после закрытия окна захвата, оставьте из '
                                       'следа SELECT, читающий `tasks`. Например, сравните начало команды '
                                       'и фрагмент FROM без учёта переносов строк, как в коде ниже.',
                                       'В фактической команде найдите `WHERE tasks.is_done = 0`. SQLite '
                                       'отбрасывает завершённые, оставляя 9 и 14. `ORDER BY tasks.id ASC` '
                                       'определяет первый номер, а `LIMIT ?` ограничивает выдачу. '
                                       'Заполнитель остаётся видимым без значения.',
                                       'В контрольной форме A SQLite отдаёт `[4, 9, 14, 21]`. Отбор и '
                                       'предел в приложении также оставят `[9]`. Одинаковый ответ '
                                       'совместим с обеими ветвями. Форма B ближе к пойманному SQL по '
                                       'смыслу.'],
                             'code': '# Продолжение внутри with TestClient, после закрытия окна захвата.\n'
                                     'matches = []\n'
                                     'for entry in trace:\n'
                                     '    normalized = " ".join(entry.statement.upper().split())\n'
                                     '    if normalized.startswith("SELECT ") and " FROM TASKS " in '
                                     'f"{normalized} ":\n'
                                     '        matches.append(entry)\n'
                                     '\n'
                                     'if len(matches) != 1:\n'
                                     '    raise RuntimeError("Нужен ровно один SELECT выдачи tasks")\n'
                                     'selection = matches[0]\n'
                                     'print(selection.statement)',
                             'checks': ['`WHERE tasks.is_done = 0`, `ORDER BY tasks.id ASC` и `LIMIT ?` '
                                        'наблюдаются на одном выбранном SELECT. Запись для PRAGMA сюда не '
                                        'попадёт.',
                                        'При замене на форму A клиентский список может совпасть, но в '
                                        'SELECT исчезнет условие статуса. Это различие видно до чтения '
                                        'плана.']}},
                 {'title': 'Прочитаем план той же команды',
                  'task': 'После закрытия окна захвата у вас есть `selection` из своего GET и тот же '
                          'рабочий Engine. `TestClient` пока остаётся открытым. В разделе «Как SQLite '
                          'добирается до строк» метод `entry.explain(engine)` строит план для уже '
                          'записанной команды. Используйте выбранную запись и её собственные параметры. '
                          'Допишите вызов в своё наблюдение и запишите три короткие фразы: что '
                          'подтверждает ответ HTTP, где видно место отбора и что говорит план о способе '
                          'доступа.\n'
                          '\n'
                          'Вспомните сравнение двух планов каталога. Объясните, почему слово `SCAN` может '
                          'встретиться у обоих вариантов выдачи и само по себе не определяет, где '
                          'отфильтровали записи. Для проверки ещё раз посмотрите, сколько команд осталось '
                          'в исходном следе GET после чтения плана.',
                  'requirements': ['План получен через `selection.explain(engine)` после закрытия окна '
                                   'захвата, внутри того же `TestClient`. `EXPLAIN QUERY PLAN` не пополнил '
                                   'след GET.',
                                   'Сохранены список id из HTTP, форма пойманного SELECT и короткое '
                                   'описание плана. Связанные значения остаются внутри записи и передаются '
                                   'SQLite отдельно при чтении плана.',
                                   'Три вывода различают выдачу клиенту, операции над строками в SQL и '
                                   'способ доступа SQLite. Объяснён случай `SCAN` с предикатом и без '
                                   'него.'],
                  'result': 'В `observe_sql.py` сохранено одно связанное наблюдение: id HTTP-ответа, форма '
                            'SELECT, план и вывод о месте отбора. Для следующей практики остаётся '
                            '`app/DBase/sql_trace.py` с окном `capture_sql(engine)`.',
                  'hint': 'При лишнем EXPLAIN в следе проверьте положение вызова относительно `with`. При '
                          'ошибке заполнителей используйте саму запись `selection`, в ней остались '
                          'исходные параметры.',
                  'answer': {'explanation': 'SQLite может пройти по таблице и применить WHERE к её '
                                            'строкам. Слово SCAN описывает обход. Предикат в фактическом '
                                            'SQL устанавливает место отбора.',
                             'steps': ['После закрытия окна захвата и до выхода из `with TestClient` '
                                       'вызовите `selection.explain(engine)`. Метод добавит `EXPLAIN QUERY '
                                       'PLAN` к тексту этой команды и передаст исходные позиционные '
                                       'значения. На учебной базе без индекса статуса в плане виден `SCAN '
                                       'tasks`.',
                                       'Запишите объяснение опыта: HTTP вернул `[9]`; SELECT отобрал '
                                       'открытые в SQLite по `WHERE tasks.is_done = 0` и применил порядок '
                                       'и предел; план показал обход таблицы.',
                                       'У полной формы запроса тоже возможен `SCAN tasks`. В ней SQLite '
                                       'может вернуть все строки, тогда ограничение по статусу произойдёт '
                                       'после чтения. Если потерять параметры, SQLite сообщит о числе '
                                       'значений. При переносе EXPLAIN внутрь окна след получит лишнюю '
                                       'запись.'],
                             'code': '# Продолжение внутри with TestClient, после закрытия окна захвата.\n'
                                     'before_plan = len(trace)\n'
                                     'plan = selection.explain(engine)\n'
                                     'print(plan)\n'
                                     'print(before_plan, len(trace))\n'
                                     '# HTTP отдал [9].\n'
                                     '# WHERE в SELECT показывает отбор по статусу в SQLite.\n'
                                     '# SCAN tasks описывает способ доступа к таблице.',
                             'checks': ['Вывод содержит `SCAN tasks` на исходной учебной схеме. До и после '
                                        'EXPLAIN в следе то же число команд, и среди них нет `EXPLAIN '
                                        'QUERY PLAN`.',
                                        'У SELECT с `WHERE` и у полного SELECT возможен одинаковый SCAN. '
                                        'Текст первого SELECT показывает условие, второго отдаёт задачу '
                                        'отбора приложению.']}}],
       'result': 'Сохранены нейтральный модуль захвата и локальные наблюдения одного GET. При обычной '
                 'работе Planner подписка отсутствует.'}],
 78: [{'title': 'Защищаем выборку Planner двумя проверками',
       'task': 'Работаем из `project/` своего Planner. Для тестов используем временную SQLite из раздела '
               '«Как перепроверить, какой сбой пойман». Возьмите свободный путь '
               '`tests/test_task_selection_contract.py` или выберите другое имя и используйте его во всех '
               'командах ниже. В этой работе есть один набор строк и два отдельных теста.',
       'tasks': [{'title': 'Подбираем данные, на которых видна ошибочная граница',
                  'task': 'Нам нужна выдача завершённых задач по убыванию номера, не больше двух. По '
                          'разделу «Готовим временную SQLite для своих тестов» соберите `selection_lab` '
                          'для пустой базы. Задайте учебный URL до входа в клиент, а фабрику подставьте '
                          'после его запуска. Затем выберите свои номера, статусы и порядок добавления. '
                          'Сохраните пары номера и статуса в своём тестовом файле, чтобы использовать их и '
                          'в следующей карточке.\n'
                          '\n'
                          'В разделе «Данные, на которых видно ошибку» две сортировки квитанций дали '
                          'разные ряды. Здесь сравните два порядка операций вокруг границы выдачи. '
                          'Запишите ряды на важных шагах и выберите набор, на котором меняется ответ. По '
                          'своему прогнозу напишите проверку ответа `GET /tasks`. Один раз подставьте '
                          'рассчитанное наблюдение для ошибочного варианта вместо фактических id перед '
                          'своим утверждением. Затем верните чтение настоящего ответа.',
                  'requirements': ['Приложение запускается на временном SQLite-адресе. Учебная фабрика '
                                   'установлена внутри открытого клиента и восстановлена до выхода из '
                                   'него.',
                                   'В тестовом файле записан собственный смешанный набор задач. Правильная '
                                   'выдача содержит две задачи.',
                                   'Для одного набора выписаны промежуточные ряды при штатном и ошибочном '
                                   'порядке операций. Итоговые списки id различаются.',
                                   '`test_task_selection_result(selection_lab)` проверяет HTTP 200 и '
                                   'точный порядок id для `GET '
                                   '/tasks?is_done=true&sort_desc=true&limit=2`.',
                                   'После временной подстановки контрольных id своё утверждение отклоняет '
                                   'ошибочный список при том же ожидании. При обычном запуске тест читает '
                                   'ответ Planner.'],
                  'result': 'Записаны два различающихся ряда. На штатном ответе тест проходит, на '
                            'контрольных id падает.',
                  'hint': 'Если оба прогноза совпали, посмотрите на строки около границы в каждом '
                          'промежуточном ряду. Какие строки доступны для следующего шага в каждом случае?',
                  'answer': {'explanation': 'Ограничение выбирает начало того ряда, который уже получен. '
                                            'Чтобы увидеть ошибку, одна и та же граница должна оставлять '
                                            'разные задачи при двух проверяемых порядках.',
                             'steps': ['Подготовьте схему на Engine из `tmp_path` и временно задайте '
                                       'приложению тот же SQLite-адрес. После входа в `TestClient` '
                                       'сохраните созданную при запуске фабрику и поставьте учебную. Перед '
                                       'GET сверяйте `session.get_bind() is engine` на короткой Session '
                                       'активной фабрики. Верните сохранённую фабрику до закрытия клиента. '
                                       'Освободите учебный Engine после выхода.',
                                       'На своих данных выпишите нужный статусный ряд в требуемом порядке. '
                                       'Отметьте, где проходит граница двух строк.',
                                       'Проследите выбранный ошибочный порядок на тех же строках. Найдите '
                                       'первую позицию, где результат отличается, или строку, которая '
                                       'пропала раньше времени.',
                                       'В `test_task_selection_result` оставьте ожидаемые id '
                                       'фиксированными. Сверьте с ними список из HTTP 200. Контрольный '
                                       'список поставьте на место наблюдения на один прогон. По отказу '
                                       'pytest видно, какая позиция или длина отличается.',
                                       'Верните чтение ответа Planner. Сохраните исходные пары id и '
                                       'статуса для второй карточки.'],
                             'checks': ['До GET активная фабрика связана с учебным Engine. При закрытии '
                                        'приложение получает обратно фабрику запуска.',
                                        'Один и тот же прогноз принимает обычный ответ и отклоняет '
                                        'контрольные id.',
                                        'В итоговом тесте наблюдаемые id снова получаются из ответа '
                                        'GET.']}},
                 {'title': 'Проверяем форму фактического SELECT',
                  'task': 'Ответ показывает, какие задачи вернулись. Теперь выясним, где выполнены отбор, '
                          'сортировка и предел. Откройте «Одинаковый ответ, разные SQL-следы» и повторите '
                          'пробу «Проверяем границы окна» из теории. Она использует `capture_sql` из '
                          '`app/DBase/sql_trace.py` и тот же Engine, который обслуживает запрос.\n'
                          '\n'
                          'Во втором тесте заселите новую временную SQLite теми же строками. В открытом '
                          'клиенте сверяйте Engine активной фабрики через `session.get_bind()`. После '
                          'записи откройте `capture_sql(engine)` и выполните тот же GET. После выхода из '
                          'окна найдите SELECT выдачи и сами сформулируйте проверку его формы. Когда '
                          'проверка готова, на один прогон сравните её с контрольным следом `SELECT id, '
                          'is_done FROM tasks ORDER BY id DESC`. Для этого контроля список id мысленно '
                          'оставляем тем же. Верните проверку фактического следа.',
                  'requirements': ['Проба границ окна показывает SELECT с заполнителем, скрытый маркер и '
                                   'неизменное число записей после закрытия.',
                                   '`test_task_selection_sql(selection_lab)` проверяет HTTP 200 и одну '
                                   'команду SELECT выдачи. Окно открыто на Engine активной фабрики '
                                   'запроса, после старта клиента и записи данных.',
                                   'В SELECT к `tasks` подтверждены условие отбора по `is_done`, '
                                   'сортировка по `id` по убыванию и предел. Каждый признак проверяется в '
                                   'соответствующей части команды.',
                                   'Регистр, переносы строк и вид параметров не меняют результат проверки '
                                   'формы.',
                                   'Контрольный след с совпадающими id отклоняется проверкой формы. '
                                   'Отдельная проверка списка id при этом сохраняет прежний результат.'],
                  'result': 'Есть отдельный тест формы SELECT. Он проходит на фактическом следе и '
                            'отклоняет контрольный.',
                  'hint': 'Найдите статус в условии отбора. Та же колонка может быть среди полей SELECT, '
                          'даже если SQLite отдаёт записи всех статусов. Если след пуст при HTTP 200, '
                          'сначала сверьте, когда подставили фабрику относительно запуска клиента.',
                  'answer': {'explanation': 'У двух вызовов могут совпасть id. Условие WHERE в фактическом '
                                            'SELECT показывает место отбора. По частям ORDER BY и LIMIT '
                                            'можно отдельно судить, где задан порядок и предел.',
                             'steps': ['Завершите запись до захвата. Если HTTP 200 вернул нужные id, а '
                                       'след пуст, проверьте `session.get_bind() is engine`. Подстановка '
                                       'перед `TestClient` может быть заменена при запуске. Поставьте '
                                       'учебную фабрику внутри клиента и только после этого откройте окно '
                                       'на её Engine.',
                                       'Возьмите из наблюдения запись SELECT к tasks и прочитайте её '
                                       '`.statement`. Приведите регистр и пробелы к одному виду, как в '
                                       'примере с ведомостью.',
                                       'Сравните фактическую форму с контрольной строкой. Колонка статуса '
                                       'там выводится, а WHERE отсутствует. Порядок сохранён, но LIMIT '
                                       'тоже отсутствует. По такому следу нельзя приписать базе все три '
                                       'операции.',
                                       'Подставьте контроль перед своим утверждением, увидьте отказ и '
                                       'верните фактическую команду. Оставьте тест ответа отдельным.'],
                             'checks': ['Форма проходит на одном реальном SELECT из окна GET.',
                                        'SELECT без отбора и предела отклоняется при неизменном ожидании '
                                        'по id.']}},
                 {'title': 'Повторяем регрессию на своей сборке Planner',
                  'task': 'По разделу «Как перепроверить, какой сбой пойман» запустите два написанных '
                          'теста из `project/`. Уберите подстановки контрольных id и SQL и пробный вызов '
                          'вывода. Сохраните настройку учебного адреса и подстановку фабрики внутри '
                          'клиента. Если тест падает, найдите первое расхождение между ожидаемыми id, '
                          'фактическим ответом и формой SELECT. Исправляйте только причину, которую '
                          'удалось подтвердить. Если текущий Planner уже проходит обе проверки, оставьте '
                          'рабочий запрос и сохраните регрессию.\n'
                          '\n'
                          'Запишите в заметку к практике фактическое имя файла и команду запуска для '
                          'следующего занятия. Если в вашей сборке есть прежняя общая проверка API и CLI, '
                          'повторите её на временной базе по команде из ТЗ. Вариант команды выбирается по '
                          'существующему файлу тестов.',
                  'requirements': ['Команда `python -m pytest -q tests/test_task_selection_contract.py` с '
                                   'фактическим именем файла запускает оба теста.',
                                   'Оба теста проходят на реальном запросе без подстановки наблюдений. У '
                                   'каждого теста временная SQLite, а сохранённая фабрика возвращается до '
                                   'закрытия клиента.',
                                   'При исправлении сохранён договор `GET /tasks`: фильтр по статусу и '
                                   'сортировка по id, предел по умолчанию 10, допустимые пределы 1–50 и '
                                   'HTTP 422 вне диапазона. Схема `tasks` и полный список CLI сохранены.',
                                   'Если файл имеющейся регрессии доступен, запускается `python -m pytest '
                                   '-q '
                                   'tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task` '
                                   'или в историческом проекте `python -m pytest -q '
                                   'tests/test_sql_runtime.py::test_cli_main_uses_configured_sql_database_not_json`.',
                                   'В заметке записана адресная команда своего файла для повторной '
                                   'проверки.'],
                  'result': 'На своей сборке получены два успешных теста. Сохранены файл регрессии и '
                            'команда запуска.',
                  'hint': 'По имени упавшего теста определите, что проверить сначала. К примеру, если id '
                          'совпадают, а тест формы падает, сравните захваченный SELECT с условием и '
                          'границей выдачи.',
                  'answer': {'explanation': 'Ранний предел может убрать нужную строку. Отбор после чтения '
                                            'способен вернуть тот же список, хотя фильтра и предела в SQL '
                                            'не будет. Эти наблюдения помогают найти место дефекта.',
                             'steps': ['Запустите свой файл из project и прочитайте результат каждой '
                                       'проверки отдельно. При пустом следе сначала проверьте, что '
                                       'фикстура подставляет фабрику после запуска клиента.',
                                       'Если есть расхождение, проследите передачу параметров от '
                                       '`get_selected_tasks` к `PlannerService.select_tasks` и '
                                       '`SqlTaskRepository.select`. Выберите место, где впервые разошлись '
                                       'ожидание и наблюдение.',
                                       'На исправной сборке сохраните оба теста без правок запроса. При '
                                       'доказанном дефекте повторите оба теста после точечного изменения и '
                                       'сверьте изменение нужного наблюдения.',
                                       'Сохраните итоговую команду с выбранным именем файла и результат '
                                       'прогона.'],
                             'code': 'python -m pytest -q tests/test_task_selection_contract.py',
                             'checks': ['На штатном Planner тесты результата и формы SELECT проходят.',
                                        'Команда из заметки повторяется на чистой временной SQLite.']}}],
       'result': 'Сохранены собственные данные и две проверки одного GET. Ошибочная граница меняет '
                 'наблюдаемые id, а отдельный тест формы обнаруживает работу, вынесенную за пределы '
                 'SELECT.'}],
 79: [{'title': 'Одна запись в API, CLI и новом процессе',
       'task': 'Работайте в своём учебном Planner. Один раз выполните подготовку из раздела «Где процесс '
               'читает и откуда взял адрес». Начинайте, когда локальная документация открылась и API '
               'отвечает на проверку доступности. Все записи опыта остаются во временном каталоге. В одной '
               'заметке сохраняйте номер задачи, поля, адреса и результаты чтений.',
       'tasks': [{'title': 'Зафиксируйте исходную запись',
                  'task': 'API отвечает. Теперь нужна одна запись, которую можно проследить до конца опыта. '
                          'В локальной документации создайте задачу с придуманным названием и допустимым '
                          'приоритетом. Запишите фактический ответ и выберите независимое чтение, чтобы '
                          'подтвердить эту запись. Опора: «Что именно доказала проверка».',
                  'requirements': ['`POST /tasks` с `title` и `priority` вернул HTTP `201`.',
                                   'Отдельный `GET /tasks/{id}` по номеру из `POST` вернул HTTP `200`.',
                                   'В заметке сохранены совпавшие `id`, `title`, `priority` и '
                                   '`is_done=false`.'],
                  'result': 'Исходное состояние одной задачи подтверждено созданием и отдельным чтением.',
                  'hint': 'Номер назначает Planner. Возьмите его из ответа на создание. Ответ `/health` не '
                          'содержит записи.',
                  'answer': {'explanation': 'Например, API создал «Проверить фонарь» с приоритетом 3 и '
                                            'номером 1. Берём этот номер из ответа и подтверждаем отдельным '
                                            'чтением. У разных задач могут совпасть названия, поэтому '
                                            'сверяем номер и поля вместе.',
                             'steps': ['Отправляем POST с названием и приоритетом, записываем HTTP 201 и '
                                       'четыре поля.',
                                       'В поле `id` для GET вводим номер из POST. В примере это 1.',
                                       'Сверяем поля второго ответа с первым. Пробный GET по отсутствующему '
                                       '№99 дал бы 404 при работающем API.'],
                             'code': 'POST /tasks\n'
                                     '{"title":"Проверить фонарь","priority":3}\n'
                                     '\n'
                                     'GET /tasks/1\n'
                                     '200 {"id":1,"title":"Проверить фонарь","priority":3,"is_done":false}',
                             'checks': ['В ответах 201 и 200 совпали номер, название, приоритет и открытое '
                                        'состояние.']}},
                 {'title': 'Найдите первое расхождение',
                  'task': 'Исходная запись есть в API, а CLI готов принять команду. Воспроизведите симптом '
                          'промаха в CLI и выберите содержательное наблюдение, которое покажет, где '
                          'разошлись показания об этой записи. В разделе «Что именно доказала проверка» '
                          'сравните проверку доступности, список и поиск по номеру.',
                  'requirements': ['Записан последний подтверждённый факт, общий для двух интерфейсов.',
                                   'Подтверждены результат CLI и ответ API на чтение одного фактического '
                                   '`id`.',
                                   'Указано, какую версию исключает выбранная проверка и какой вопрос '
                                   'остаётся открытым.'],
                  'result': 'Наблюдается адресный промах CLI при подтверждённом попадании API.',
                  'hint': 'Что нового сообщит повторный `/health`? Если промах произошёл в списке, прямое '
                          'чтение позволит проверить другое предположение.',
                  'answer': {'explanation': 'До поиска известно, что оба входа отвечают. Для №1 API уже '
                                            'вернул строку. Прямой `find` показал промах без параметров '
                                            'списка. Это исключает фильтр списка как объяснение.',
                             'steps': ['Сохраняем HTTP 200 и поля исходного GET. Запуск CLI подтверждаем '
                                       'появлением меню.',
                                       'Вводим `find`, затем 1. CLI сообщает, что запись не найдена. Выходим '
                                       'через `exit`.',
                                       'Сравниваем результаты одного id. Причину ищем дальше, поскольку '
                                       'старт обоих процессов был успешен.'],
                             'code': 'GET /tasks/1 → 200, Проверить фонарь, 3, false\n'
                                     '\n'
                                     'CLI:\n'
                                     'find\n'
                                     '1\n'
                                     'Задача не найдена.\n'
                                     'exit',
                             'checks': ['Для одного номера зафиксированы два разных результата адресного '
                                        'чтения.']}},
                 {'title': 'Установите причину промаха',
                  'task': 'Выберите гипотезу о причине адресного промаха. В разделе «Где процесс читает и '
                          'откуда взял адрес» доступны локальный след фактического файла, настройка оболочки '
                          'и безопасное чтение строки. Воспользуйтесь ими, чтобы объяснить промах '
                          'наблюдениями за запусками и данными. Исходную строку и файлы пока не меняйте.',
                  'requirements': ['Сняты фактические пути `main` при чтениях одного `id` через API и CLI.',
                                   'Сопоставлено содержимое этих файлов по тому же номеру. Если номер '
                                   'встретился в обоих файлах, сравнены и остальные поля.',
                                   'Названо действующее значение и объяснено, как его получил '
                                   'соответствующий процесс при запуске.'],
                  'result': 'Причина объяснена фактическими путями и независимой сверкой исходной записи.',
                  'hint': 'Текущий терминал и уже открытый процесс могут показывать разные адреса. Что '
                          'измеряет каждый из инструментов?',
                  'answer': {'explanation': 'В примере API прочитал `atlas.sqlite3`, а `find` использовал '
                                            '`compass.sqlite3`. Схема готова в обоих файлах, но только в '
                                            "первом лежит `(1, 'Проверить фонарь', 3, 0)`. Подготовка "
                                            'следующей базы сменила `DATABASE_URL` в оболочке после старта '
                                            'API. Новому CLI досталось это значение; уже запущенный API '
                                            'сохранил первое.',
                             'steps': ['Читаем `SQLite main` отдельно в выходе CLI и в журнале API после '
                                       'GET.',
                                       'Готовой командой чтения проверяем №1 на каждом из этих путей. '
                                       'Получаем строку в `atlas.sqlite3` и `None` в `compass.sqlite3`.',
                                       'Читаем текущую настройку PowerShell. Она совпадает с показанием CLI. '
                                       'Теперь гипотеза объясняет как чтение, так и успешные старты.'],
                             'code': "Get-Content -LiteralPath (Join-Path $labRoot 'first.out')\n"
                                     "& $labPython -c 'from app.core.config import load_settings; "
                                     "print(load_settings().database_url)'\n"
                                     '\n'
                                     "GET main: atlas.sqlite3 → (1, 'Проверить фонарь', 3, 0)\n"
                                     'CLI main: compass.sqlite3 → None',
                             'checks': ['Одна цель содержит исходную строку, другая её не содержит. Связь '
                                        'каждой цели с процессом установлена по `main`.']}},
                 {'title': 'Исправьте запуск и перечитайте',
                  'task': 'Измените только доказанную настройку лабораторного запуска. Оставьте обе базы и '
                          'исходную запись на месте. Повторите прежние чтения через API и CLI. Выберите '
                          'нужный процесс по разделу «Где процесс читает и откуда взял адрес». После сверки '
                          'уберите временный диагностический вывод из своего исходника.',
                  'requirements': ['Указан подтверждённый полный адрес для исправленного запуска; новой '
                                   'записи взамен исходной не создавали.',
                                   'Повторные `find` и `GET /tasks/{id}` подтверждают общий путь `main` и '
                                   'совпадение четырёх исходных полей.',
                                   'Диагностический фрагмент снят. Если импорт `text` использовался до '
                                   'опыта, он сохранён.'],
                  'result': 'Оба интерфейса находят исходную открытую задачу в одном файле.',
                  'hint': 'Подумайте, какой процесс должен заново получить переменную окружения и где это '
                          'можно проверить.',
                  'answer': {'explanation': 'В опыте корректный файл уже открыт работающим API. Исправляем '
                                            'адрес следующего CLI и повторяем поиск. Повторный POST с тем же '
                                            'названием создал бы другой объект.',
                             'steps': ['После `exit` в учебном PowerShell задаём абсолютный SQLite URL, '
                                       'построенный из доказанного пути API.',
                                       'Запускаем CLI, ищем №1, сохраняем путь и поля. Повторяем GET через '
                                       'прежний API.',
                                       'Записываем совпадение и выходим из CLI. Убираем временный вывод из '
                                       'исходника. Старый API продолжит работать до остановки.'],
                             'code': "$confirmedFile = Join-Path $labRoot 'atlas.sqlite3'\n"
                                     "$env:DATABASE_URL = 'sqlite:///' + $confirmedFile.Replace('\\', '/')\n"
                                     '& $labPython -m app.main\n'
                                     '\n'
                                     '# В меню CLI\n'
                                     'find\n'
                                     '1\n'
                                     'exit',
                             'checks': ['Свежий CLI прочитал №1 и прежние поля. Пути обоих интерфейсов '
                                        'совпали до изменения статуса.']}},
                 {'title': 'Измените задачу и прочитайте через API',
                  'task': 'Оба интерфейса нашли открытую задачу. Отметьте её выполненной штатной командой '
                          'CLI. Пока первый API работает, получите свежее состояние той же строки. Опора: '
                          '«Как проверить сохранение после перезапуска». Сохраните результат и подготовьте '
                          'через знакомый POST ещё две открытые задачи с другими названиями для сверки '
                          'сводки.',
                  'requirements': ['`done` выполнена для исходного `id` из заметки.',
                                   'Следующий GET через первый API вернул тот же `id`, прежние название и '
                                   'приоритет и `is_done=true`.',
                                   'Созданы ровно две дополнительные открытые задачи. Их номера отличаются '
                                   'от исходного и друг от друга.'],
                  'result': 'Действующий API подтвердил изменение исходной строки; данные для сводки готовы.',
                  'hint': 'При старом статусе сначала проверьте результат CLI, введённый номер и '
                          'используемый файл.',
                  'answer': {'explanation': 'После фиксации другой процесс видит изменение исходной строки. '
                                            'Сравниваем новый GET со снимком до команды. Сообщение CLI само '
                                            'по себе ещё не показывает, что увидел API.',
                             'steps': ['В CLI с проверенной настройкой вводим `done`, затем 1. Читаем ответ '
                                       'и выходим.',
                                       'Первый API по №1 отдаёт «Проверить фонарь», приоритет 3 и true. До '
                                       'изменения было false при тех же полях.',
                                       'Через этот же API создаём две открытые задачи, например «Заказать '
                                       'пломбы» и «Проверить кабель». Записываем их id.'],
                             'code': 'done\n'
                                     '1\n'
                                     'exit\n'
                                     '\n'
                                     'GET /tasks/1\n'
                                     '200 {"id":1,"title":"Проверить фонарь","priority":3,"is_done":true}\n'
                                     '\n'
                                     'POST /tasks {"title":"Заказать пломбы","priority":2}\n'
                                     'POST /tasks {"title":"Проверить кабель","priority":4}',
                             'checks': ['У исходной строки изменился только статус. Два дополнительных POST '
                                        'вернули 201 и разные id.']}},
                 {'title': 'Перечитайте из нового API и сохраните команды',
                  'task': 'Остановите именно первый учебный API. Запустите новый на проверенном адресе, '
                          'самостоятельно перечитайте исходный номер и сопоставьте поля со снимком после '
                          'CLI. По разделу «Как проверить сохранение после перезапуска» сверяйте сводку '
                          'отдельно, повторите прежние проверки и закрепите рабочий запуск в своём '
                          '`project/README.md`.',
                  'requirements': ['Старый процесс завершён; зафиксированы проверенный адрес, новый PID и '
                                   'успешный старт.',
                                   'Новый GET вернул исходный `id` и весь ожидаемый снимок, включая '
                                   '`is_done=true`.',
                                   'Готовый контроль прочитал ту же SQLite. Поля HTTP `total=3`, `open=2`, '
                                   '`done=1` совпали с независимым подсчётом.',
                                   'Запущены доступный общий smoke, прежние проверки отказов, отката и SQL и '
                                   'свой адресный тест запроса.',
                                   'README фиксирует `project/`, Python и venv, зависимости, общий '
                                   'безопасный URL, явный init новой базы, обычные API/CLI, проверочное '
                                   'чтение, остановку и команду общего теста.'],
                  'result': 'Новый API прочитал ту же завершённую строку. Сводка сверена, рабочие команды '
                            'сохранены в README.',
                  'hint': 'При HTTP 404 после старта проверьте адрес нового процесса до записей. Сравните '
                          'номер и все поля со своим снимком.',
                  'answer': {'explanation': 'После остановки первого API новый процесс читает №1 на '
                                            'сохранённом адресе. Название и приоритет прежние, статус true. '
                                            'На другом файле `/health` тоже может отвечать, но прямое чтение '
                                            '№1 там даст промах.',
                             'steps': ['Записываем старый PID, останавливаем свой API и ждём его завершения. '
                                       'Проверяем действующий URL перед новым запуском.',
                                       'Запускаем API с другим именем журнала. После health читаем №1 и '
                                       'сравниваем четыре поля со снимком после CLI.',
                                       'Готовой сверкой читаем тот же SQLite-файл. Получаем две открытые и '
                                       'одну завершённую, всего три. Сверяем `/stats` по именам: при обмене '
                                       'open и done проверка заметит расхождение.',
                                       'Повторяем доступные регрессии. В README переносим обычные команды с '
                                       'безопасным примером адреса. Указываем, что каталог файла создают до '
                                       'init, а оба терминала получают один URL. Для остановки записываем '
                                       '`Ctrl+C` и `exit`.'],
                             'code': '# Повторный запуск в учебном PowerShell\n'
                                     '$labApi.Id\n'
                                     'if (-not $labApi.HasExited) { Stop-Process -InputObject $labApi }\n'
                                     '$labApi.WaitForExit()\n'
                                     "& $labPython -c 'from app.core.config import load_settings; "
                                     "print(load_settings().database_url)'\n"
                                     "$labApi = Start-LabApi 'second'\n"
                                     '$labApi.Id\n'
                                     'Invoke-RestMethod "$labBase/tasks/1"\n'
                                     'Invoke-RestMethod "$labBase/stats"\n'
                                     '\n'
                                     '# Обычный запуск для README из project/\n'
                                     'py -3.12 -m venv .venv\n'
                                     '.venv/Scripts/python.exe -m pip install -r requirements-dev.txt\n'
                                     '# Выберите существующий учебный каталог и задайте URL в обоих '
                                     'терминалах.\n'
                                     "$env:DATABASE_URL = 'sqlite:///C:/Temp/example/planner.sqlite3'\n"
                                     '.venv/Scripts/python.exe -m app.DBase.init_db\n'
                                     '.venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 '
                                     '--port 8079\n'
                                     '# Из другой оболочки с тем же URL\n'
                                     '.venv/Scripts/python.exe -m app.main\n'
                                     '.venv/Scripts/python.exe -m pytest -q '
                                     'tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task',
                             'checks': ['Новый API прочитал №1 «Проверить фонарь», приоритет 3, true.',
                                        'Подписанные счётчики SQLite и HTTP совпали: 3 всего, 2 открыто, 1 '
                                        'завершено.',
                                        'README содержит команды установки, init, штатных запусков и '
                                        'проверки для другой чистой учебной цели.']}}],
       'result': 'Причина расхождения установлена и устранена. Исходная задача подтверждена новым '
                 'API-процессом, сводка сверена, а рабочий проект и README готовы к проверке в чистой '
                 'копии.'}],
 80: [{'title': 'Передача работающего DB Planner',
       'task': 'Вы отдаёте коллеге работающий Planner. У него есть документация и переданные файлы. Возьмите '
               'свой проект после проверки API и CLI, собственный README и тест общего чтения из практики о '
               'сбоях хранилища. Выберите отдельную свободную папку для проверки. Ведите заметку рядом с '
               'ней: опись, использованные команды и результаты. Рабочий проект и данные сохраняйте отдельно '
               'от опыта.',
       'result': 'Можно передать код, зависимости, тесты, проверенный README и безопасный образец. На новом '
                 'адресе создана исходная пустая схема. Оба интерфейса открыли один файл, повторный init '
                 'сохранил строку, контроль старого поведения выполнен.',
       'tasks': [{'title': 'Отбираем файлы до установки',
                  'task': 'Откройте «Что действительно получает другой человек» и возьмите способ сверки '
                          'двух списков. Соберите копию в отдельной новой папке. Прочитайте свой README, '
                          'проверьте относительные ссылки, команды и нужные им файлы. Посмотрите, что '
                          'действительно попало в копию, включая скрытые файлы. Запишите выбранную папку и '
                          'опись рядом с копией до создания среды.',
                  'requirements': ['В копии есть исходный код, README, оба файла требований, безопасный '
                                   '`.env.example` и `tests/` с файлом `test_storage_availability.py`. В нём '
                                   'есть знакомая проверка общего чтения.',
                                   'Файлы, которые используют команды, ссылки README и тесты, входят в '
                                   'опись. Образец содержит только учебные локальные настройки.',
                                   'Фактическое содержимое сверено до установки. Рабочие `.env`, `.venv`, '
                                   'базы, архивы, журналы и секреты исключены.'],
                  'result': 'Сохранена опись новой папки. По ней можно начать установку и открыть каждый '
                            'нужный ресурс из README.',
                  'hint': 'Возьмите одну ссылку из README и пройдите её прямо в копии. Сравните и файлы, '
                          'которые нашлись, и файлы, которых там быть не должно.',
                  'answer': {'explanation': 'В комплект склада попали пять файлов, но инструкция ссылается '
                                            'на отсутствующий бланк `labels.csv`. Лишний `shift.db` занял '
                                            'место бланка в описи. Счётчик файлов совпал, а проверка по '
                                            'именам и назначению обнаружила оба разрыва.',
                             'steps': ['До установки открыть новую папку и сверить фактические файлы с '
                                       'принятой описью.',
                                       'Разрешить ссылки инструкции и зависимости команд. В примере добавить '
                                       'отсутствующий `labels.csv`.',
                                       'Убрать из передаваемого комплекта рабочий журнал и ещё раз открыть '
                                       'все относительные ссылки.'],
                             'code': 'Get-ChildItem -Force -Recurse -File | Select-Object -ExpandProperty '
                                     'FullName',
                             'checks': ['В описи есть реальный файл, на который указывает ссылка.',
                                        'В чистой копии нет перенесённого рабочего журнала или среды.']}},
                 {'title': 'Проверяем свою среду и учебный адрес',
                  'task': 'Продолжите из папки с проверенной описью. По своему README установите зависимости '
                          'в новую среду и выберите путь к будущей синтетической SQLite. Подготовьте '
                          'родительский каталог. Если выбранное имя занято, возьмите другое свободное имя. '
                          'Сверьте Python и нормализованный URL в двух свежих терминалах. Вернитесь к '
                          'разделу «Откуда берутся Python, зависимости и адрес»: там видно, почему настройка '
                          'первого окна не меняет окружение второго. Запишите первое расхождение и исправьте '
                          'соответствующее место в инструкции, если оно требуется для воспроизведения.',
                  'requirements': ['Запуск идёт из папки чистого Planner с `app/` и `tests/`. Указанная '
                                   'версия Python доступна, исполняемый файл находится в новой `.venv`, '
                                   'зависимости установлены по README.',
                                   'Записан полный путь к новой SQLite. Родительский каталог существует, '
                                   'будущий файл отсутствует.',
                                   'В обоих терминалах `load_settings().database_url` указывает на этот '
                                   'адрес. Исправленный шаг заново проверен, если был найден пробел.'],
                  'result': 'Записаны путь к новому Python, доступные зависимости и один нормализованный '
                            'адрес в двух терминалах.',
                  'hint': 'Сравните два вывода интерпретатора и две строки URL отдельно. Какой факт '
                          'разошёлся первым?',
                  'answer': {'explanation': 'Оба окна могут использовать правильный Python, а второе при '
                                            'этом взять адрес обычной базы. Переменная PowerShell из первого '
                                            'окна наследуется только его дочерними процессами.',
                             'steps': ['Сравнить путь исполняемого файла с новой папкой среды и проверить '
                                       'доступность пакетов через этот Python.',
                                       'До запуска процессов определить свободный адрес и отдельно показать '
                                       'настройки каждого окна.',
                                       'Если во втором окне остался обычный адрес, в инструкции явно '
                                       'повторить присвоение для второго терминала. Снова сравнить обе '
                                       'строки до init.'],
                             'code': '.\\.venv\\Scripts\\python.exe -c "import sys; print(sys.executable)"\n'
                                     '.\\.venv\\Scripts\\python.exe -m pip show SQLAlchemy pytest\n'
                                     '.\\.venv\\Scripts\\python.exe -c "from app.core.config import '
                                     'load_settings; print(load_settings().database_url)"',
                             'checks': ['Обе строки URL показывают выбранный полный путь.',
                                        'Вывод `pip show` относится к пакету той же среды. Будущий файл ещё '
                                        'отсутствует.']}},
                 {'title': 'Подтверждаем исходную схему до запуска',
                  'task': 'В «Пустой файл, пустая таблица и явный init» найдите читатель схемы. Дайте ему '
                          'выбранный путь и имя таблицы. Зафиксируйте отсутствие файла или таблицы. Затем '
                          'выполните явный init по своему README. Тем же читателем сравните найденную схему '
                          'с исходной моделью и сохраните DDL и результат чтения в заметку. API и CLI будут '
                          'нужны в следующем шаге. Если инструкция пропустила init или направила его в '
                          'другое место, исправьте её и перепроверьте на свободной цели.',
                  'requirements': ['Читатель не создаёт отсутствующую SQLite, факт до init записан. Явный '
                                   'init выполнен на выбранном адресе.',
                                   'После init существует исходная `tasks` с шестью обязательными колонками '
                                   '`id/title/priority/is_done/tags/title_normalized`, первичным ключом `id '
                                   'INTEGER`, проверками `id > 0` и `priority` от 1 до 5. SQL DEFAULT, '
                                   'внешние ключи и дополнительные индексы отсутствуют.',
                                   'Наблюдение сохранено до первого запуска API или CLI. В `tasks` ноль '
                                   'строк.'],
                  'result': 'Сохранены полный путь, DDL, свойства колонок и нулевой счётчик на выбранном '
                            'файле.',
                  'hint': 'Вспомните пустой файл журнала и пустой бланк. Что показал читатель до запуска '
                          'init?',
                  'answer': {'explanation': 'Пустой файл открывается, но его каталог схемы не содержит '
                                            'нужной таблицы. После явной подготовки мы получаем определение '
                                            'таблицы и ноль строк. Каждый из этих фактов можно наблюдать '
                                            'отдельно.',
                             'steps': ['Прочитать состояние того же пути до init. Если имя свободно, '
                                       'читатель сообщает, что файл отсутствует.',
                                       'Проверить нормализованный URL и выполнить модуль явного init по '
                                       'инструкции.',
                                       'Перечитать тот же физический файл. Найти шесть имён и свойства '
                                       'колонок, два ограничения в DDL и нулевой счётчик. Сохранить сведения '
                                       'для повтора.'],
                             'code': '.\\.venv\\Scripts\\python.exe -m app.DBase.init_db',
                             'checks': ['Есть DDL исходной `tasks` и `Строк: 0`. При запуске init на другом '
                                        'адресе эти факты не появятся на выбранной цели.',
                                        'Открывающий файл без таблицы даёт отдельный результат «Таблица '
                                        'отсутствует».']}},
                 {'title': 'Сверяем файлы, открытые API и CLI',
                  'task': 'Запустите обычный API и CLI по своему README, каждый с уже проверенным URL. В '
                          '«Как узнать, какой файл открыл каждый процесс» найдите способ читать `main` на '
                          'той Session, которая выполнила чтение. Найдите оба обычных чтения в коде копии, '
                          'временно подключите вывод пути и проведите `GET /tasks` и `list`. Сравните три '
                          'полных пути: два фактических и тот, где создана пустая схема. Проверьте отдельно '
                          'пустой список в каждом интерфейсе. Снимите временный вывод и завершите процессы. '
                          'Если пришлось уточнить README, повторите проверку.',
                  'requirements': ['HTTP и CLI запущены обычными точками входа, без подмены Session.',
                                   'У каждого чтения есть непустой путь `main` из его рабочей Session. Оба '
                                   'совпадают с выбранной целью.',
                                   'HTTP вернул `200` и `[]`, в CLI виден пустой список. Временный '
                                   'диагностический вывод удалён до первой записи.'],
                  'result': 'Сохранены два фактически открытых пути и два результата пустого чтения. Код '
                            'готов к обычному запуску без временной диагностики.',
                  'hint': 'Два пустых списка выглядят одинаково. Где в двух обычных путях всё ещё доступна '
                          'Session конкретного чтения?',
                  'answer': {'explanation': 'Два уже подготовленных пустых файла дают одну и ту же выдачу. '
                                            'Разные `main` покажут, что один процесс читает зеркало. Окно '
                                            'Settings проверяет настройку, а путь из Session связывает факт '
                                            'с настоящим чтением.',
                             'steps': ['Временный вывод поставить в `app/DBase/repositories.py` после '
                                       'выполнения SELECT в методах `select` и `list` до выхода из текущей '
                                       'Session. Добавить нужные импорты, если их ещё нет.',
                                       'После обычного `GET /tasks` и команды `list` записать полные пути из '
                                       'терминалов. Если один путь ведёт к зеркалу, исправить настройки '
                                       'этого запуска и заново прочитать список.',
                                       'Снять только свои диагностические строки и добавленные для них '
                                       'импорты. Завершить CLI через `exit` и свой API через Ctrl+C. Перед '
                                       'записью использовать свежий запуск.'],
                             'code': '# Импорты модуля\n'
                                     'from pathlib import Path\n'
                                     'from sqlalchemy import text\n'
                                     '\n'
                                     '# Внутри метода после обычного SELECT\n'
                                     'for _, schema, opened in self.session.execute(text("PRAGMA '
                                     'database_list")):\n'
                                     '    if schema == "main":\n'
                                     '        print(Path(opened).resolve() if opened else "(без файла)")',
                             'checks': ['С пустым зеркалом оба списка пусты, но один путь отличается. После '
                                        'исправления совпадают оба пути и целевой файл.',
                                        'Диагностический код удалён перед использованием записывающих '
                                        'команд.']}},
                 {'title': 'Перечитываем ту же строку после повторного init',
                  'task': 'В «Как доказать сохранность после повторного init» обратите внимание, когда снят '
                          'первый снимок. На проверенной цели создайте через штатный POST одну синтетическую '
                          'задачу со своим названием и приоритетом. Возьмите выданный id и сохраните четыре '
                          'публичных поля отдельным чтением. По тому же id снимите все шесть значений из '
                          'SQLite и сохраните исходную DDL из предыдущего шага. Завершите свои процессы. '
                          'Повторите init на том же адресе, затем прочитайте запись новым API и CLI, а '
                          'полную строку и схему сравните с сохранёнными. В чистой копии выполните точную '
                          'проверку общего чтения и полный набор. Зафиксируйте в README команды, которые '
                          'удалось воспроизвести. Из проверочной папки отберите только те файлы, которые '
                          'потребуются для нового запуска.',
                  'requirements': ['После `POST /tasks` получен `201`. Перед повтором зафиксированы реальный '
                                   'id, четыре публичных значения, полный кортеж и исходная DDL.',
                                   'Повторный init использовал тот же файл. Новый `GET /tasks/{id}` и CLI '
                                   '`find` находят прежний id с прежними публичными полями. Полная строка и '
                                   'схема совпали со снимками.',
                                   'Из чистой копии прошли `.\\.venv\\Scripts\\python.exe -m pytest -q '
                                   'tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task` '
                                   'и `.\\.venv\\Scripts\\python.exe -m pytest -q`.',
                                   'Проверенный README указывает корень проекта, Python и зависимости, '
                                   'создание родительского каталога, URL в обоих терминалах, явный init, '
                                   'штатные API и CLI, чтение, завершение процессов и доступную команду '
                                   'теста. Учебная база и наблюдения остаются вне передаваемого комплекта.'],
                  'result': 'В заметке совпадают все значения, полученные до повтора и после него. '
                            'Передаваемый проект можно запустить по README на другом свободном учебном '
                            'адресе.',
                  'hint': 'Если совпал id, проверьте остальные публичные поля, внутренние значения и DDL по '
                          'сохранённым записям. При расхождении сначала сравните адреса повторного init и '
                          'читателей.',
                  'answer': {'explanation': 'Совпадение номера не исключает изменения значения. Сопоставьте '
                                            'строку целиком: четыре публичных значения видны в интерфейсах, '
                                            'а два внутренних значения сохраняются в SQLite. Зелёный pytest '
                                            'использует собственную временную базу; после него сравнение '
                                            'наблюдений о вашей базе всё ещё нужно.',
                             'steps': ['Создать одну новую задачу обычным POST и взять id из ответа. '
                                       'Получить публичный снимок и прочитать по этому id полную SQL-строку. '
                                       'Вместе с предыдущей DDL записать значения до init.',
                                       'Остановить писателей, сверить адрес, повторить явный init. Запустить '
                                       'обычные API и CLI, найти исходный id в каждом. Ещё раз прочитать '
                                       'SQL-строку и DDL тем же способом.',
                                       'Сопоставить оба публичных представления и все SQL-значения со '
                                       'снимками. Если номер сохранился, а название или внутреннее поле '
                                       'изменилось, результат расходится.',
                                       'Запустить точную регрессию и полный набор из чистой копии. В '
                                       'итоговый комплект положить воспроизводимую инструкцию, код, '
                                       'зависимости, образец и тесты.'],
                             'code': '# Наблюдение из Python в своей проверочной папке\n'
                                     'import sqlite3\n'
                                     'from contextlib import closing\n'
                                     'from pathlib import Path\n'
                                     '\n'
                                     'file = Path(input("Путь к проверенной базе: ")).resolve(strict=True)\n'
                                     'task_id = int(input("ID задачи: "))\n'
                                     'with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as '
                                     'db:\n'
                                     '    schema = db.execute("SELECT sql FROM sqlite_schema WHERE '
                                     'type=\'table\' AND name=\'tasks\'").fetchone()\n'
                                     '    row = db.execute(\n'
                                     '        "SELECT id, title, priority, is_done, tags, title_normalized '
                                     'FROM tasks WHERE id = ?",\n'
                                     '        (task_id,),\n'
                                     '    ).fetchone()\n'
                                     '    print(schema)\n'
                                     '    print(row)',
                             'checks': ['При повторном чтении на исправной цели DDL и кортеж совпадают. '
                                        'Другой адрес, отсутствующая строка или изменённое поле выявляются '
                                        'сравнением.',
                                        'Точный тест сообщает `1 passed`, полный набор завершён успешно. '
                                        'Синтетический файл исключён из комплекта передачи.']}}]}]}
