from typing import Any

MANUAL_PRACTICE: dict[int | str, list[dict[str, Any]]] = {
    "71.1": [
        {
            "title": "Заменяем существующую задачу в SQLite",
            "task": "У нашей задачи уже есть id и внутренние теги. Добавим в свой Planner SQL-замену для прежнего PUT. Проверим, какие данные вернулись в ответе и какие сохранились в базе.",
            "tasks": [
                {
                    "title": "Реализуем один метод replace",
                    "task": "Начнём со своего проекта после практики создания задач в SQLite. В каталоге `project` уже есть `app/service.py`, `app/DBase/repositories.py` и `tests`. Для запуска используем тот же Python с зависимостями проекта, что и для предыдущих проверок.\n\nГотовая подготовка\n\nВ `project/app/service.py` найдём класс `PlannerService` и заменим в нём только метод `replace_task` готовым фрагментом. Область записи начинается до поиска задачи. Сервис уже собирает и проверяет новую Task с прежними id и тегами.\n\n```python\n    def replace_task(self, task_id, *, title, priority, is_done):\n        with self.write_scope():\n            current = self.get_task(task_id)\n            updated = Task(\n                id=current.id,\n                title=title,\n                priority=priority,\n                is_done=is_done,\n                tags=current.tags.copy(),\n            )\n            stored = self.repository.replace(updated)\n            if stored is None:\n                self._raise_task_not_found(current.id)\n            return stored\n```\n\nСкопируем следующий блок целиком в `project/tests/test_put_replace_lab.py`. Если такой файл уже есть, сохраним проверку под свободным именем `test_put_replace_local.py` или с числовым суффиксом и подставим это имя в команды. Проверка сама создаёт временную SQLite, запускает запросы к нашему приложению и показывает значения. Штатный JSON проверяется на временном файле.\n\n```python\nfrom contextlib import contextmanager\n\nimport pytest\nfrom fastapi import Depends\nfrom sqlalchemy import select\nfrom sqlalchemy.orm import Session\n\nfrom app.DBase.models import Base, TaskRow\nfrom app.DBase.repositories import SqlTaskRepository\nfrom app.DBase.session import create_session_factory\nfrom app.api import app\nfrom app.core.dependencies import get_db, get_planner\nfrom app.core.engine import make_engine\nfrom app.main import build_service, sql_service_context\nfrom app.models import Task\n\n\ndef snapshot(factory):\n    with factory() as db:\n        return {\n            row.id: (row.title, row.priority, row.is_done, row.tags, row.title_normalized)\n            for row in db.scalars(select(TaskRow))\n        }\n\n\n@pytest.fixture\ndef sql_lab(tmp_path):\n    path = tmp_path / \"put-lab.sqlite3\"\n    engine = make_engine(f\"sqlite:///{path.as_posix()}\")\n    try:\n        Base.metadata.create_all(engine)\n        factory = create_session_factory(engine)\n        with sql_service_context(factory) as planner:\n            target = planner.add_task(\"Черновик\", 2, tags=[\"личное\"])\n            neighbor = planner.add_task(\"ЁЛКА В КЛАССЕ\", 4, tags=[\"архив\"])\n        yield factory, target.id, neighbor.id\n    finally:\n        engine.dispose()\n\n\nclass LabAbort(Exception):\n    pass\n\n\n@contextmanager\ndef sql_provider(factory, abort=False):\n    def provide_planner(db: Session = Depends(get_db)):\n        @contextmanager\n        def write_scope():\n            with db.begin():\n                yield\n                if abort:\n                    raise LabAbort(\"Отмена перед commit\")\n\n        return build_service(repository=SqlTaskRepository(db), write_scope=write_scope)\n\n    absent = object()\n    previous_factory = getattr(app.state, \"session_factory\", absent)\n    previous_overrides = app.dependency_overrides.copy()\n    app.state.session_factory = factory\n    app.dependency_overrides[get_planner] = provide_planner\n    try:\n        yield\n    finally:\n        app.dependency_overrides.clear()\n        app.dependency_overrides.update(previous_overrides)\n        if previous_factory is absent:\n            del app.state.session_factory\n        else:\n            app.state.session_factory = previous_factory\n\n\ndef test_baseline_create_and_read(sql_lab):\n    factory, target_id, neighbor_id = sql_lab\n    rows = snapshot(factory)\n    print(\"Исходные строки по id:\", rows)\n    assert target_id > 0 and neighbor_id > 0 and target_id != neighbor_id\n    assert rows == {\n        target_id: (\"Черновик\", 2, False, [\"личное\"], \"черновик\"),\n        neighbor_id: (\"ЁЛКА В КЛАССЕ\", 4, False, [\"архив\"], \"ёлка в классе\"),\n    }\n    with sql_service_context(factory) as planner:\n        assert planner.get_task(target_id).tags == [\"личное\"]\n        assert planner.get_task(neighbor_id).tags == [\"архив\"]\n\n\ndef test_put_and_fresh_read(sql_lab, api_client):\n    factory, target_id, _ = sql_lab\n    before = snapshot(factory)\n    print(\"До PUT:\", before)\n    cases = [\n        (\"ПЛАН ДНЯ\", 3, True, \"план дня\"),\n        (\"ЁЛКА В КЛАССЕ\", 1, False, \"ёлка в классе\"),\n    ]\n    with sql_provider(factory):\n        for title, priority, done, title_key in cases:\n            body = {\"title\": title, \"priority\": priority, \"is_done\": done}\n            response = api_client.put(f\"/tasks/{target_id}\", json=body)\n            print(\"Ответ PUT:\", response.status_code, response.json())\n            assert response.status_code == 200\n            assert response.json() == {\"id\": target_id, **body}\n            after = snapshot(factory)\n            print(\"После PUT (новая Session):\", after)\n            assert after == {\n                **before,\n                target_id: (title, priority, done, [\"личное\"], title_key),\n            }\n\n\ndef test_missing_and_invalid_do_not_write(sql_lab, api_client):\n    factory, target_id, neighbor_id = sql_lab\n    before = snapshot(factory)\n    missing_id = max(target_id, neighbor_id) + 100\n    with sql_provider(factory):\n        response = api_client.put(\n            f\"/tasks/{missing_id}\",\n            json={\"title\": \"Промах\", \"priority\": 3, \"is_done\": True},\n        )\n        print(\"Отсутствующий id:\", response.status_code, response.json())\n        assert response.status_code == 404\n        assert response.json() == {\"detail\": \"Task not found\"}\n        assert snapshot(factory) == before\n\n        response = api_client.put(f\"/tasks/{target_id}\", json={\"title\": \"Неполное тело\", \"priority\": 3})\n        print(\"Неполное тело:\", response.status_code)\n        assert response.status_code == 422\n        assert snapshot(factory) == before\n\n    absent_task = Task(id=missing_id, title=\"Промах\", priority=3, is_done=True, tags=[])\n    with factory.begin() as db:\n        assert SqlTaskRepository(db).replace(absent_task) is None\n    assert snapshot(factory) == before\n\n\ndef test_cancel_before_commit(sql_lab, api_client):\n    factory, target_id, _ = sql_lab\n    before = snapshot(factory)\n    with sql_provider(factory, abort=True):\n        with pytest.raises(LabAbort, match=\"Отмена перед commit\"):\n            api_client.put(\n                f\"/tasks/{target_id}\",\n                json={\"title\": \"Временная правка\", \"priority\": 1, \"is_done\": True},\n            )\n    after = snapshot(factory)\n    print(\"После отмены:\", after)\n    assert after == before\n\n\ndef test_json_put_uses_temp_storage(sql_lab, api_client):\n    factory, _, _ = sql_lab\n    before = snapshot(factory)\n    with sql_provider(factory):\n        pass\n\n    create = api_client.post(\"/tasks\", json={\"title\": \"JSON старт\", \"priority\": 2})\n    assert create.status_code == 201\n    task_id = create.json()[\"id\"]\n    body = {\"title\": \"JSON итог\", \"priority\": 5, \"is_done\": True}\n    response = api_client.put(f\"/tasks/{task_id}\", json=body)\n    print(\"JSON PUT:\", response.status_code, response.json())\n    assert response.status_code == 200\n    assert response.json() == {\"id\": task_id, **body}\n    assert api_client.get(f\"/tasks/{task_id}\").json() == {\"id\": task_id, **body}\n    assert snapshot(factory) == before\n```\n\nИз каталога `project` запустим только проверку исходной базы.\n\n```text\npython -m pytest -q -s tests/test_put_replace_lab.py -k baseline\n```\n\nВ строке «Исходные строки по id» увидим «Черновик» с тегом «личное» и «ЁЛКА В КЛАССЕ» с тегом «архив». Оба id получили при создании. Если эта проверка падает, сначала сверим свои SQL create и get.\n\nТеперь в `project/app/DBase/repositories.py` допишем один метод `replace` внутри существующего `SqlTaskRepository`. Вход уже содержит проверенную Task. Требуется сохранить её публичные значения в существующей строке, сохранить id и внутренние теги, обновить технический заголовок и вернуть обычную Task. Для отсутствующего id нужно сохранить прежний договор об ошибке.\n\nПрогоним весь готовый файл. Сравним номера, публичные поля и теги до и после обоих PUT. Обратим внимание на второй запрос: флаг меняется с true на false, а название теперь совпадает с названием соседней задачи. Отдельные сценарии проверяют отсутствующий id, неполное тело, отмену и прежний JSON PUT.\n\n```text\npython -m pytest -q -s tests/test_put_replace_lab.py\n```",
                    "requirements": [
                        "`SqlTaskRepository.replace(self, task: Task) -> Task | None` принимает проверенную Task и адресно обновляет существующую строку по task.id. Возвращается обычная Task. Метод использует имеющуюся Session и flush. Область подтверждает сервис.",
                        "id и содержимое tags сохранены. title, priority и is_done совпадают с входной Task, title_normalized равен Python task.title.lower(). Состав строк и соседняя запись сохранены.",
                        "Первый PUT для «Черновик» отвечает 200, сохраняет прежний id и возвращает «ПЛАН ДНЯ», приоритет 3 и true. Отдельное чтение видит тег «личное» и «план дня». Ответ содержит только id, title, priority и is_done.",
                        "Второй PUT отвечает 200 с «ЁЛКА В КЛАССЕ», приоритетом 1 и false. В новой Session видны прежний id, тег «личное» и «ёлка в классе». Совпадение заголовка с соседом допустимо.",
                        "Промах внутри репозитория даёт None, PUT по отсутствующему id отвечает 404 с «Task not found». Неполное тело даёт 422. В этих случаях и при управляемой отмене до commit снимок строк совпадает с исходным.",
                        "Готовый прогон подтверждает прежние SQL create/get и JSON PUT. Маршрут, HTTP-схемы и штатный выбор JSON сохраняются."
                    ],
                    "result": "Обе команды проходят. В выводе видны 200, 404, 422, прежний id и тег «личное», новый false и служебное название «ёлка в классе». Соседняя задача не меняется. Можем объяснить, откуда взялись id, tags и технический заголовок.",
                    "hint": "Вернёмся к схеме источников полей и выберем значение, которое разошлось. Какой источник даёт его для новой строки? Для формы поиска и преобразования пригодится знакомый SQL get, для технического заголовка и flush посмотрим на свой create.",
                    "answer": {
                        "explanation": "В проверке с задачей «Эскиз» (id 23, тег «черновое») запрос меняет заголовок на «МАКЕТ» и закрывает задачу. Сервис уже выбрал исходную задачу и подготовил проверенную модель с её id и копией тегов. Репозиторий записывает эту модель в одну существующую строку. Новый ресурс читает «МАКЕТ», техническое «макет» и тег «черновое» под тем же id.",
                        "steps": [
                            "Уточним, какую запись нужно открыть. Идентификатор task.id выбирает уже существующую строку. Если get вернул None, вернём None до любых присваиваний.",
                            "Перенесём публичные значения напрямую из проверенной Task. Это сохраняет в том числе явное False. id остаётся у строки, теги берём из копии списка Task, которую подготовил сервис.",
                            "Вычислим техническое имя из нового title тем же lower(), что использовали при создании. Flush отправляет изменения в текущую область. Преобразуем ORM-строку в обычную Task; подтверждение выполнит сервис.",
                            "Сравним ответ с независимым чтением. Ответ содержит только публичные поля. Чтение также подтверждает тег и новое техническое имя. Отсутствующий id даёт 404, а контрольный отказ до commit сохраняет исходную строку."
                        ],
                        "code": "    def replace(self, task: Task) -> Task | None:\n        row = self.session.get(TaskRow, task.id)\n        if row is None:\n            return None\n\n        row.title = task.title\n        row.priority = task.priority\n        row.is_done = task.is_done\n        row.tags = task.tags.copy()\n        row.title_normalized = task.title.lower()\n\n        self.session.flush()\n        return task_from_row(row)",
                        "checks": [
                            "Источник id и tags понятен, изменение адресовано одной строке.",
                            "Два последовательных PUT различают явное false, потерю тега и устаревшее техническое имя.",
                            "Свежая Session видит результат завершённой области. Промах и отказ не оставляют записи."
                        ]
                    }
                }
            ],
            "result": "Прежний PUT на тестовой SQL-сборке заменяет публичные поля. Новая Session подтверждает сохранённые id и теги и согласованный технический заголовок."
        }
    ],
    72: [{'title': 'Применяем частичную правку в SQLite',
          'task': 'В вашем Planner уже работают поиск по id, PUT и транзакция на Session репозитория. В учебной '
                  'SQLite есть задача 120 «Старый План» с приоритетом 4, статусом True и меткой «внутр», а рядом '
                  'задача 121 «Сосед» с приоритетом 1, статусом False и меткой «рядом». Откроем в теории «Диагностика '
                  'для локального запуска» и выполним указанный там входной PUT. Проверим, что свежее чтение '
                  'показывает новый заголовок с прежними метками. Каждый следующий вызов диагностики получает '
                  'отдельную копию исходных данных.',
          'tasks': [{'title': 'Выбираем поля и сохраняем частичное изменение',
                     'task': 'Клиент хочет открыть задачу 120. Он прислал false для статуса, null для заголовка и '
                             'пропустил приоритет. Откроем «Какие значения просит изменить PATCH». Проследим на '
                             'бланке, почему False остаётся заданным значением и как чтение из словаря позволяет '
                             'взять прежнее значение для пропущенной графы.\n'
                             '\n'
                             'В `app/service.py` изменим `PlannerService.patch_task`. Сами определим, как хранить '
                             'заданные значения трёх полей и по этому выбору собрать следующую `Task` из запроса и '
                             'найденной задачи. Пока сохраним прежнее поведение для пустого входа. В «Когда запрос '
                             'обходится без записи» посмотрим, как граница Session связана с первым поиском и свежим '
                             'чтением. В `app/DBase/repositories.py` добавим `update` в `SqlTaskRepository`, '
                             'используя знакомую запись полной задачи из «Что сохраняется при замене». Существующий '
                             '`replace` уже умеет найти строку по id и сохранить её внутренние данные.\n'
                             '\n'
                             'Откроем свежий Python, вставим диагностический блок и вызовем `observe_write("PATCH", '
                             '{"is_done": False, "title": None})`. Сравним строки до и после. Затем на исходной '
                             'задаче со статусом False отдельно проверим `observe_write("PATCH", {"title": "  Новый '
                             'План  "}, start_done=False)` и `observe_write("PATCH", {"priority": 3}, '
                             'start_done=False)`. Первый вызов с False меняет статус, второй проверяет заголовок и '
                             'поисковую копию, третий проверяет приоритет при сохранённом статусе.',
                     'requirements': ['`PlannerService.patch_task(task_id, *, title=None, priority=None, '
                                      'is_done=None)` использует существующую `write_scope` с первого поиска. '
                                      'Репозиторий использует ту же Session.',
                                      'Заданные значения трёх публичных полей выбираются в сервисе. Пропуск и None '
                                      'сохраняют прежнее значение, False применяется. Номер и метки берутся из '
                                      'найденной задачи.',
                                      '`SqlTaskRepository.update(self, task: Task) -> Task | None` использует рабочую '
                                      'запись `replace`. Результат соответствует договору репозитория, транзакцией '
                                      'владеет сервис.',
                                      'Ответ PATCH и свежий GET содержат прежние публичные поля. Свежая строка '
                                      'показывает назначенное изменение, прежние метки и правильную поисковую копию. '
                                      'Соседняя строка совпадает с исходной.'],
                     'result': 'Первый PATCH даёт HTTP 200 и оставляет у 120 «Старый План», приоритет 4 и метку '
                               '«внутр», а статус в новой Session становится False. Для переименования видны «Новый '
                               'План» и «новый план», для отдельной правки приоритета видны 3 и прежний False. Строка '
                               '121 сохраняет все шесть полей.',
                     'hint': 'В «Какие значения просит изменить PATCH» отметим, откуда пришло каждое из трёх '
                             'значений. Для отсутствующего приоритета исходное значение уже находится в прочитанной '
                             'задаче. Вспомним, в какой момент PUT начинал область записи.',
                     'answer': {'explanation': 'Сначала получаем исходную задачу. Из запроса выбираем значения, '
                                               'которые клиент действительно задал. Тогда полную задачу можно собрать '
                                               'из двух источников и передать прежней операции записи.',
                                'steps': ['Начинаем с `with self.write_scope()` и внутри ищем 120. На этом шаге '
                                          'получаем «Старый План», 4, True и «внутр». Если сделать поиск раньше, '
                                          'Session может уже начать транзакцию и повторное открытие области вызовет '
                                          'ошибку.',
                                          'Для основного запроса маршрут передаёт только `is_done=False`. Заголовок с '
                                          'null отфильтрован, а приоритета в теле нет. В `requested` оставляем '
                                          'значения, которые отличаются от None. Это даёт словарь с одним ключом '
                                          '`is_done` и значением False. При проверке `if is_done` он бы остался '
                                          'пустым.',
                                          'У `requested.get("is_done", current.is_done)` находится ключ с False. Для '
                                          'title и priority ключей нет, поэтому получаем старые «Старый План» и 4. Id '
                                          '120 и копию меток берём из исходной задачи. Оставляем существующую '
                                          'проверку пустого ввода до следующей карточки.',
                                          'Из `update` передаём готовую Task знакомому `replace`. Он находит строку '
                                          'по id, хранит прежние метки и пересчитывает поисковую копию при смене '
                                          'заголовка. Если строки нет, сервис по-прежнему сообщает об отсутствующей '
                                          'задаче.',
                                          'На выходе из `with` область подтверждает запись. Диагностика выполняет '
                                          'свежий GET и отдельно читает строки задач в другой Session. Если вместо '
                                          'False сохранился True, проверьте, сравнивает ли код поле именно с None.'],
                                'code': '# app/service.py, внутри PlannerService\n'
                                        'def patch_task(self, task_id, *, title=None, priority=None, is_done=None):\n'
                                        '    with self.write_scope():\n'
                                        '        current = self.get_task(task_id)\n'
                                        '        requested = {}\n'
                                        '        if title is not None:\n'
                                        '            requested["title"] = title\n'
                                        '        if priority is not None:\n'
                                        '            requested["priority"] = priority\n'
                                        '        if is_done is not None:\n'
                                        '            requested["is_done"] = is_done\n'
                                        '\n'
                                        '        if title is None and priority is None and is_done is None:\n'
                                        '            return current\n'
                                        '\n'
                                        '        updated = Task(\n'
                                        '            id=current.id,\n'
                                        '            title=requested.get("title", current.title),\n'
                                        '            priority=requested.get("priority", current.priority),\n'
                                        '            is_done=requested.get("is_done", current.is_done),\n'
                                        '            tags=current.tags.copy(),\n'
                                        '        )\n'
                                        '        stored = self.repository.update(updated)\n'
                                        '        if stored is None:\n'
                                        '            self._raise_task_not_found(current.id)\n'
                                        '        return stored\n'
                                        '\n'
                                        '# app/DBase/repositories.py, внутри SqlTaskRepository\n'
                                        'def update(self, task: Task) -> Task | None:\n'
                                        '    return self.replace(task)',
                                'checks': ['Вызов `observe_write("PATCH", {"is_done": False, "title": None})` даёт '
                                           '200, один вызов update, один UPDATE и сохранённый False.',
                                           'С `start_done=False` для `{"title": "  Новый План  "}` свежая строка '
                                           'содержит «Новый План» и «новый план». Для `{"priority": 3}` она содержит '
                                           'приоритет 3 и прежний False.',
                                           'В каждом наблюдении сравним обе строки до и после. У 120 сохраняются id и '
                                           'метка «внутр». У 121 совпадают id, название, приоритет, статус, метки и '
                                           'поисковая копия.']}},
                    {'title': 'Возвращаем текущую задачу без записи',
                     'task': 'В первой карточке вы уже выбрали значения для будущей версии задачи. Вернёмся к «Когда '
                             'запрос обходится без записи». Проследим, почему пустой список поручений на базе не '
                             'выполняет ни одного UPDATE, а найденную заявку всё равно можно прочитать. Затем '
                             'заглянем в «Какие значения просит изменить PATCH» и вспомним, почему единственный False '
                             'остаётся заданным значением.\n'
                             '\n'
                             'В `app/service.py` изменим решение о сохранении в `PlannerService.patch_task`. '
                             'Используем тот же отбор, который применили при сборке `Task`, чтобы определить, '
                             'требуется ли запись. Выберем место возврата так, чтобы по существующему номеру клиент '
                             'увидел текущую задачу, а по неизвестному номеру всё ещё получил 404.\n'
                             '\n'
                             'В свежем Python вставим диагностический блок и выполним `observe_write("PATCH", {}, '
                             'start_done=False)` и `observe_write("PATCH", {"title": None, "priority": None, '
                             '"is_done": None}, start_done=False)`. Оба вызова воспроизводят открытое состояние после '
                             'основного PATCH первой карточки. Сравним снимки и оба счётчика. Проверим промах через '
                             '`observe_write("PATCH", {}, task_id=999, start_done=False)`. Затем '
                             '`observe_write("PATCH", {"is_done": False})` проверит сохранение False на отдельной '
                             'задаче со стартовым True.',
                     'requirements': ['В `PlannerService.patch_task` выбор ветки без записи использует тот же отбор '
                                      'заданных значений, что и сборка Task. Отсутствующий номер возвращает прежний '
                                      '404 и при пустом теле.',
                                      'Пустое тело и тело только с null для существующей задачи возвращают 200 и '
                                      'текущее состояние. В каждом случае оба счётчика равны 0, обе строки совпадают '
                                      'со снимками до запроса.',
                                      'Явный False на исходном True вызывает сохранение. После запроса свежая Session '
                                      'показывает False.'],
                     'result': 'Два запроса без новых значений возвращают прежнюю задачу. В каждом наблюдении видно '
                               '«Вызовы update: 0» и «SQL UPDATE: 0». Пустой запрос к 999 даёт 404. Запрос с явным '
                               'False на исходном True меняет статус в базе.',
                     'hint': 'Сначала сравним два случая из «Когда запрос обходится без записи». Если снимки '
                             'совпадают, но число вызовов update равно 1, найдём место, где код всё ещё обращается к '
                             'репозиторию для сохранения.',
                     'answer': {'explanation': 'Найденная Task уже содержит состояние, которое должно вернуться при '
                                               'пустом вводе. Решение о записи можно принять по тому же словарю, из '
                                               'которого мы берём новые значения для этой Task.',
                                'steps': ['Внутри области ищем задачу 120 и получаем статус False. Поиск выполняется '
                                          'для любого тела, поэтому для 999 сработает прежний путь к 404 прежде, чем '
                                          'образуется успешный ответ.',
                                          'Для `{}` маршрут не передал полей. Для тела из трёх null графы передали, '
                                          'но фильтр убрал None. В обоих случаях отбор в сервисе оставляет '
                                          '`requested` пустым.',
                                          'После отбора возвращаем `current`, если весь словарь пуст. Вызов `update` '
                                          'находится дальше и в эту ветку не попадает. Чтение было, но ни одного '
                                          'поручения сохранить строку не возникло.',
                                          'Проверяем границу на отдельном True. Для явного False словарь содержит '
                                          'один ключ, поэтому проверка пустоты не срабатывает. Новый статус '
                                          'подтверждается при выходе из области и виден свежей Session.',
                                          'Сравниваем измерения. Если отправить прежнюю Task на сохранение, '
                                          'SQLAlchemy может заметить совпадение значений и не выполнять UPDATE. '
                                          'Диагностика всё равно покажет один вызов update, поэтому проверяем оба '
                                          'нуля.'],
                                'code': '# app/service.py, внутри PlannerService.patch_task\n'
                                        '# После поиска и заполнения requested замените прежнюю проверку трёх '
                                        'аргументов.\n'
                                        '# Этот фрагмент остаётся внутри with self.write_scope().\n'
                                        '        if not requested:\n'
                                        '            return current\n'
                                        '\n'
                                        '# В Python-консоли, после команды подготовки наблюдений\n'
                                        'observe_write("PATCH", {}, start_done=False)\n'
                                        'observe_write("PATCH", {"title": None, "priority": None, "is_done": None}, '
                                        'start_done=False)\n'
                                        'observe_write("PATCH", {}, task_id=999, start_done=False)\n'
                                        'observe_write("PATCH", {"is_done": False})',
                                'checks': ['Из обоих запросов к 120 без новых значений получаем 200, прежний False, '
                                           'прежние строки 120 и 121 и два нуля.',
                                           'Из запроса к 999 с пустым телом получаем 404 и два нуля.',
                                           'В положительном контроле с исходным True и явным False получаем 200, один '
                                           'вызов update и один SQL UPDATE. Свежий снимок показывает False.']}}],
          'result': 'Свежая Session подтверждает частичную правку. Когда запрос не задаёт ни одного нового значения, '
                    'найденная задача возвращается без вызова update и без SQL UPDATE.'}],
    "72.1": [
        {
            "title": "Одна задача в SQLite: завершение, повтор и удаление",
            "task": "Работаем в своём Planner из каталога `project/`. Для каждой карточки открываем свежий сеанс Python из этого каталога, вставляем ячейку из блока «Диагностика Planner для локального запуска» в конце теории и запускаем нужный `start_lab`. Это временная учебная SQLite. В ней первая строка 17 «Проверить АРХИВ», вторая 42 «Сдать КЛЮЧИ». У обеих есть приоритет, статус, метки и поисковое название. Номера и исходные снимки видны при запуске. Код, написанный в редакторе, остаётся в Planner. Временные базы между карточками переносить не нужно. Исходный просмотр при старте проверяет только запуск оснастки.",
            "result": "Первое завершение меняет выбранный флаг, повтор обходится без записи, SQL-удаление отдаёт прежнюю Task или None, а после удаления свежий читатель видит исчезновение выбранного id и полный снимок соседа.",
            "tasks": [
                {
                    "title": "Сохраняем завершение выбранной задачи",
                    "task": "Задача 42 пока открыта. Команда должна закрыть именно её, чтобы новый читатель увидел результат. В `app/service.py` доработаем существующий `PlannerService.mark_done` для SQL-репозитория. Заголовок «Как выбрать одну запись и проверить результат после записи» показывает, когда открыть область и почему один ответ ещё не доказывает запись. В разделе «Когда завершение требует записи» посмотрим, что меняется при первом вызове. Сохраним знакомые проверку id и результат поиска. Возьмём `start_lab()` и вызовем `done(42)`. Потом прочитаем обе строки через `read_rows(17, 42)`. В случае `done(99)` по-прежнему ожидается предметная ошибка.",
                    "requirements": [
                        "Первый поиск и возможное сохранение происходят в одной области `write_scope` текущего сервиса.",
                        "Для открытой задачи изменяется `is_done`. Сохранение происходит через общий `update(Task)`. Метод возвращает закрытую Task после подтверждения операции.",
                        "У 42 в новой Session прежние id, title, priority, tags и title_normalized. Все поля 17 совпадают с исходным снимком.",
                        "После сброса счётчиков `done(99)` даёт `TaskNotFoundError`. Записи в SQLite нет."
                    ],
                    "result": "Исходная 42 имеет False. После `done(42)` ответ и свежий снимок дают True, остальные значения совпадают. `counts()` показывает `read_in_scope=True`, `update=1` и `UPDATE=1`. При отдельной пробе 99 оба счётчика записи равны нулю.",
                    "hint": "Где в существующем методе происходит первое обращение к репозиторию? Когда результат в объекте Python становится доступен другой Session?",
                    "answer": {
                        "explanation": "Рабочая область принадлежит сервису. Он охватывает ею решение целиком, чтобы следующий читатель увидел изменение уже после возврата команды.",
                        "steps": [
                            "Начните с исходного снимка 42: «Сдать КЛЮЧИ», приоритет 4, False, метки «срок» и «офис». У 17 другой id и уже True.",
                            "В `mark_done` разместите существующее обращение `get_task(task_id)` внутри `write_scope`. Оно проверяет id и возвращает именно нужную Task. Выход из области при ошибке завершает операцию до сохранения.",
                            "У открытой 42 вызов `Task.mark_done()` изменит только её флаг. Передайте эту Task в существующий `repository.update`, проверьте полученный результат тем же способом, что и раньше, и используйте сохранённую Task в ответе.",
                            "Если поиск выполнен до области, SQLAlchemy может уже открыть транзакцию и следующий begin даст ошибку. Если область вообще не охватывает изменение, метод может вернуть True, а следующий читатель увидит False.",
                            "Для 99 поиск заканчивается предметной ошибкой. До предметного изменения и общего update управление не доходит."
                        ],
                        "code": "with self.write_scope():\n    task = self.get_task(task_id)",
                        "checks": [
                            "После `start_lab()` выполните `done(42)`, затем `read_rows(17, 42)` и `counts()`. Свежая 42 закрыта, сосед цел, счётчики 1 / 1.",
                            "В том же сеансе сбросьте счётчики перед `done(99)`. Будут предметная ошибка и 0 / 0."
                        ]
                    }
                },
                {
                    "title": "Подтверждаем повтор без записи",
                    "task": "Закрытие уже сохраняет True. Теперь доведём ранний выход в SQL-ветке `PlannerService.mark_done` и сами соберём последовательность для повтора. По разделу «Когда завершение требует записи» определим, какой прежний флаг должны увидеть и почему полезно считать отдельно вызовы update и запросы SQLite. В новом сеансе возьмём `start_lab()`. Первой командой закроем 42. Выберем момент сброса счётчиков, затем повторим команду в этой же базе, сохраним оба результата и прочитаем свежий снимок. Если прежний ранний выход уже вошёл в новую область записи при первом изменении, используем его в опыте.",
                    "requirements": [
                        "Правило повтора применяется к существующей задаче после её поиска в области записи. Закрытая задача возвращается без перехода к update.",
                        "Проверочная последовательность ученика сбрасывает оба счётчика после первой операции и до второй. Во втором вызове используется тот же id.",
                        "Сверяются содержимое первой и второй Task, состояние в новой Session и два независимых счётчика."
                    ],
                    "result": "Оба вызова возвращают закрытую 42 с одинаковыми полями. После повтора снимок не изменился, `update=0` и `UPDATE=0`. Пара `1 / 0` выявляет лишний вызов репозитория даже при прежнем снимке.",
                    "hint": "Какое из чисел изменится, если репозиторий получил задачу со старыми значениями и SQLAlchemy не отправила UPDATE?",
                    "answer": {
                        "explanation": "Повтор поддерживает уже достигнутое состояние. Изменение ветки можно увидеть отдельно от того, что ORM сочла нужным записать.",
                        "steps": [
                            "После запуска с открытой 42 сначала вызовите done и сохраните обычную Task. Состояние базы к этому моменту уже True.",
                            "В существующем методе найдите прежний ранний возврат. Убедитесь, что значение флага взято из найденной задачи внутри области записи, а возврат происходит до изменения и вызова update.",
                            "Сбросьте счётчики только сейчас, непосредственно перед вторым done. Если сбросить их раньше, результат первой записи попадёт в измерение повтора.",
                            "Сравните содержимое через `to_dict()`. При одинаковых задачах и паре 1 / 0 проверьте, почему код дошёл до update. ORM может не записать прежнее значение, но вызов уже произошёл."
                        ],
                        "code": "first = done(42)\nreset_counts()\nsecond = done(42)\nprint(first.to_dict() == second.to_dict())\nprint(read_rows(17, 42))\nprint(counts())",
                        "checks": [
                            "Видны True у обеих Task, True у 42 из нового чтения и оба нуля. Счётчик первого закрытия в этот результат не вошёл."
                        ]
                    }
                },
                {
                    "title": "Получаем Task после удаления",
                    "task": "Закрытую 42 нужно убрать по номеру. Репозиторию при успехе надо отдать прежние данные задачи в обычной Task, пригодной после закрытия Session. В `app/DBase/repositories.py` напишем `SqlTaskRepository.delete(task_id)`. В `app/service.py` подключим его к области записи сервиса `PlannerService.delete_task` и сохраним прежнее поведение для промаха. Раздел «Что остаётся после удаления» показывает получение независимой квитанции из ещё доступной строки и проверку отсутствия. Из «Как выбрать одну запись и проверить результат после записи» переносим границу чтения и удаления. Запустим `start_lab(closed=True)` и сравним `repo_delete(42)`, `repo_delete(99)` и `delete(99)`. При необходимости перед каждым отдельным вызовом сбросим счётчики.",
                    "requirements": [
                        "SQL-репозиторий выбирает одну `TaskRow` по id и поддерживает результат `Task | None`. При промахе он пропускает DELETE.",
                        "При успехе данные обычной Task получены до удаления. `Session.delete` получает выбранную ORM-строку. Предметные поля Task читаются после закрытия Session.",
                        "Сервис владеет областью операции и сохраняет обработку `is None` и прежний результат успешного вызова. Роут и CLI не требуют изменения."
                    ],
                    "result": "`repo_delete(42)` возвращает обычную Task: 42 / Сдать КЛЮЧИ / 4 / True / [«срок», «офис»]. После закрытия Session доступны её поля или `to_dict()`. Для 99 репозиторий возвращает None при `DELETE=0`, сервис выдаёт `TaskNotFoundError`.",
                    "hint": "Какой объект ещё содержит исходные поля до удаления? Какой объект сможет прочитать вызывающий код после выхода из рабочей Session?",
                    "answer": {
                        "explanation": "У удаления два независимых результата: строка покидает базу, а её прежнее состояние остаётся у вызывающего кода в обычной Task.",
                        "steps": [
                            "В `SqlTaskRepository.delete` по 42 найдите ORM-строку. Если тот же поиск выполнен с 99, он вернёт None. В этой ветви закончите работу, пока нечего передать в SQLAlchemy.",
                            "Из найденной строки 42 получите обычную Task знакомым `task_from_row`. На этом шаге у неё прежние id, название, приоритет, True и обе метки.",
                            "Передайте найденную `TaskRow` в `Session.delete`, а собранную `Task` отдайте вызывающему коду. У `Session.delete` служебный результат None, поэтому его возврат перепутал бы успех с промахом.",
                            "В `PlannerService.delete_task` сохраните валидацию и поместите вызов репозитория в `write_scope`. Прежняя проверка `is None` остаётся на сервисной границе и превращает промах 99 в предметную ошибку.",
                            "Возврат булева теряет данные задачи. Возврат ORM-строки нарушает договор Task и оставляет результат связанным с правилами Session."
                        ],
                        "code": "removed = task_from_row(row)\nself.session.delete(row)",
                        "checks": [
                            "Из свежего `start_lab(closed=True)` сохраните `removed = repo_delete(42)` и прочитайте `removed.to_dict()` за пределами рабочей Session.",
                            "После сброса `repo_delete(99)` даёт None и 0 DELETE, а `delete(99)` поднимает прежнюю предметную ошибку."
                        ]
                    }
                },
                {
                    "title": "Доказываем, какой id исчез",
                    "task": "В свежем сеансе `start_lab(closed=True)` снова показывает обе строки. Возьмём второй id как цель, а другой как соседа. Напишем свои вызовы проверки: сохраним полные снимки, передадим выбранный id в `delete`, затем перечитаем оба номера через `read_rows`. В разделе «Что остаётся после удаления» разберём, почему пустой результат нужен для цели, а полное совпадение для соседа. В «Как выбрать одну запись и проверить результат после записи» возьмём способ отделить завершившуюся операцию от нового чтения. После своего вывода запустим ячейку из «Готовый прогон HTTP и CLI» и вызовем `regression()`.",
                    "requirements": [
                        "В проверке явно названы ключ цели и ключ соседа из исходной пары. До удаления оба ключа указывают на существующие строки.",
                        "Вызов сервиса использует только выбранный ключ. После подтверждения одна новая Session получает обе строки.",
                        "Для выбранного ключа результат None. Для соседа сравниваются все шесть полей: id, title, priority, is_done, tags и title_normalized.",
                        "Отдельная готовая регрессия подтверждает пустой HTTP 204, GET и повтор DELETE с 404, выбор другого id и прежние сообщения CLI."
                    ],
                    "result": "После удаления цель 42 отсутствует. У соседа 17 по-прежнему название «Проверить АРХИВ», приоритет 2, True, метка «бумаги» и поисковое название «проверить архив». Регрессия на новой базе сообщает, что HTTP и CLI сохранили договор.",
                    "hint": "Если исчез первый id, какая из двух проверок это покажет? Если id соседа сохранился, но пропала метка, какое сравнение даст расхождение?",
                    "answer": {
                        "explanation": "Чтобы доказать адресность, нужен снимок под каждым ключом. Изменение размера таблицы не даёт адреса удалённой строки.",
                        "steps": [
                            "Из исходной пары выберите 42 целью и 17 соседом. В исходном словаре по каждому номеру лежит кортеж из шести полей.",
                            "Вызовите `delete` с выбранным ключом. Затем перечитайте оба ключа вызовом `read_rows`. Оснастка сама откроет новую Session после выхода из рабочей области.",
                            "Сравните значение выбранного ключа с `None` и отдельно сравните оба снимка соседа. При ошибке с первым id значение 17 стало бы None, а 42 осталась бы. Потерю метки или изменение поисковой копии заметит сравнение целых кортежей.",
                            "Запустите подготовленную проверку совместимости. Она удалит 17 в отдельном HTTP-сценарии, проверит GET и повтор удаления и проведёт прежние команды CLI по 42."
                        ],
                        "code": "target_id, neighbor_id = 42, 17\nbefore = read_rows(target_id, neighbor_id)\nassert before[target_id] is not None and before[neighbor_id] is not None\ndelete(target_id)\nafter = read_rows(target_id, neighbor_id)\nassert after[target_id] is None\nassert after[neighbor_id] == before[neighbor_id]\nregression()",
                        "checks": [
                            "42 отсутствует, кортеж 17 совпадает, затем отдельный сценарий подтверждает HTTP 204 без тела, оба 404 и прежние сообщения CLI."
                        ]
                    }
                }
            ]
        }
    ],
    75: [{'title': 'Переводим Planner на одну SQLite',
      'task': 'Работаем в отдельной учебной копии своего Planner, из её корня project/. После репетиции '
              'обычные API и CLI снова читают JSON. В разделе «Что мы будем делать в практике» откройте '
              '«Данные и команды учебного запуска». Оттуда возьмите две синтетические задачи, адреса, '
              'команды, отчёт сравнения и способ запуска. Рабочие базы, архивы и процессы остаются вне '
              'учебной копии. Записывайте путь, id, значения полей и результат каждой проверки в заметку '
              'к упражнению.',
      'tasks': [{'title': 'Снимаем архив после остановки записей',
                 'task': 'В разделе «Замораживаем источник и проверяем архив» найдите, почему совпавшая '
                         'последняя запись не делает старую копию актуальной. В Planner после репетиции '
                         'изменился приоритет задачи №34. При этом задача №52 во всех копиях осталась '
                         'прежней.\n'
                         '\n'
                         'Запустите обычные API и CLI на учебном JSON и проверьте чтение №52. '
                         'Посмотрите, какой JSON указан в каждом обычном запуске. Завершите оба '
                         'процесса, чтобы источник больше не менялся. Выберите новое имя в '
                         'data/archives/ и снимите точную копию фактического источника. Готовой командой '
                         'сравните байты кандидатов с замороженным файлом. Запишите, какой файл будете '
                         'импортировать, и объясните решение.',
                 'requirements': ['Сохранены фактический JSON-путь, сведения об остановке обоих '
                                  'писателей и отдельный путь нового архива.',
                                  'Готовое сравнение всего нового архива с замороженным источником даёт '
                                  'равенство.',
                                  'Сравнение старой копии с тем же источником даёт расхождение при той '
                                  'же №52. Допуск указан только для совпавшей копии.'],
                 'result': 'Есть точный архив, его путь и решение о допуске к импорту. В наблюдениях '
                           'отдельно виден устаревший кандидат.',
                 'hint': 'Результат по №52 полезен для проверки запуска. При выборе архива вернитесь к '
                         'действующему пути и побайтному сравнению полного файла.',
                 'answer': {'explanation': 'Остановленные API и CLI больше не изменяют учебный JSON. '
                                           'После этого сравнение двух файлов относится к одному '
                                           'состоянию источника. Старая пробная копия проходит чтение '
                                           '№52, хотя у №34 в ней сохранился приоритет 2.',
                            'steps': ['В обоих терминалах используем обычные настройки из теории. '
                                      'Контрольный GET /tasks/52 и CLI find 52 показывают «Уточнить '
                                      'срок». В каждом окружении выводим эффективный JSON-путь. В '
                                      'примере он заканчивается на data/tasks.json.',
                                      'Завершаем учебный CLI через exit и останавливаем учебный API '
                                      'через Ctrl+C. Теперь копия не пересечётся с новой записью.',
                                      'Выбираем свободное имя data/archives/tasks-before-sqlite.json. '
                                      'Копируем из действующего data/tasks.json. Сравниваем архив с '
                                      'исходником. Результат True допускает эту копию.',
                                      'Сравниваем data/rehearsal/tasks.json с тем же источником. False '
                                      'возникает из-за №34 с приоритетом 2 вместо 4. При неожиданном '
                                      'результате заново сверяем адреса и паузу, выбираем свободное имя '
                                      'и переснимаем копию.'],
                            'code': '# PowerShell, из учебной копии project/\n'
                                    'python -c "from app.core.config import load_settings; '
                                    'print(load_settings().json_path.resolve())"\n'
                                    "New-Item -ItemType Directory -Path 'data/archives' -Force\n"
                                    'if (Test-Path -LiteralPath '
                                    "'data/archives/tasks-before-sqlite.json') { throw 'Выберите "
                                    "свободное имя архива' }\n"
                                    "Copy-Item -LiteralPath 'data/tasks.json' -Destination "
                                    "'data/archives/tasks-before-sqlite.json'\n"
                                    'python -c "from pathlib import Path; import sys; '
                                    'print(Path(sys.argv[1]).read_bytes() == '
                                    'Path(sys.argv[2]).read_bytes())" \'data/tasks.json\' '
                                    "'data/archives/tasks-before-sqlite.json'\n"
                                    'python -c "from pathlib import Path; import sys; '
                                    'print(Path(sys.argv[1]).read_bytes() == '
                                    'Path(sys.argv[2]).read_bytes())" \'data/tasks.json\' '
                                    "'data/rehearsal/tasks.json'",
                            'checks': ['Сравнение с новым архивом печатает True, со старой копией '
                                       'печатает False.',
                                       'В заметке записаны путь остановленного источника и отличный от '
                                       'него путь допущенного архива.']}},
                {'title': 'Проверяем результат импорта по данным',
                 'task': 'В разделе «Сверяем содержимое после импорта» найдите, как сравнить записи по '
                         'ключам и полям, даже когда число строк совпадает. Возьмите допущенный архив и '
                         'выберите отдельную SQLite внутри учебной копии. На её адресе подготовьте '
                         'исходную схему. Готовым чтением из «Данные и команды учебного запуска» '
                         'подтвердите, что tasks пуста, затем явно импортируйте архив.\n'
                         '\n'
                         'Прочитайте полный отчёт в новом чтении SQLite. Сами решите, можно ли '
                         'использовать этот файл. Если в отчёте есть различие, укажите первый id и поле, '
                         'например потерянный тег при совпавших ключах. В заметке сохраните выбранный '
                         'путь и отчёт до любых прикладных записей.',
                 'requirements': ['Путь подтверждённого архива и путь SQLite различаются. В '
                                  'подготовленной tasks до импорта 0 строк.',
                                  'После завершения импорта отчёт сверяет набор id и значения id, title, '
                                  'priority, is_done, tags, title_normalized. Поисковое поле '
                                  'сравнивается с title.lower() из архивной записи.',
                                  'Для совпавшего импорта оба списка различающихся id пусты и различий в '
                                  'полях 0. При отличии данные не допущены, первый отличившийся id и '
                                  'поле названы.'],
                 'result': 'Есть путь заполненной SQLite и полный отчёт с решением. Обычные запуски ещё '
                           'остаются на JSON.',
                 'hint': 'При равном числе строк прочитайте две части отчёта: наборы id и значения полей '
                         'у совпавшего id.',
                 'answer': {'explanation': 'Побайтное равенство подтвердило архив относительно JSON. '
                                           'Сверка импорта сравнивает этот архив с представлением задач '
                                           'в SQLite, включая поле, которое вычислил импортёр.',
                            'steps': ['Выбираем новый файл, например data/cutover.sqlite3, и задаём его '
                                      'в DATABASE_URL. Запускаем init. Через inspect_import с одним '
                                      'аргументом проверяем, что в tasks 0 строк.',
                                      'Передаём импортёру путь допущенного архива и путь созданной базы. '
                                      'После завершения команды запускаем inspect_import с обоими '
                                      'путями. Отдельное чтение видит сохранённый результат.',
                                      "У №34 ищем «Купить бумагу», приоритет 4, False и ['офис']. Для "
                                      'поля поиска ожидаем «купить бумагу». У №52 сверяем вторую полную '
                                      'строку. Пустые списки ключей и ноль различий дают допуск.',
                                      "Если в №34 сравнение показывает tags ['офис'] → [], сохраняем "
                                      'расхождение и останавливаемся. Счётчик 2 и одинаковые id могли бы '
                                      'скрыть эту потерю. После выяснения причины выбираем новую пустую '
                                      'цель и повторяем импорт.'],
                            'code': '# PowerShell, подготовка файла\n'
                                    "$env:DATABASE_URL = 'sqlite:///data/cutover.sqlite3'\n"
                                    'python -m app.DBase.init_db\n'
                                    '# В оболочке Python с оснасткой из теории:\n'
                                    '# inspect_import("data/cutover.sqlite3")\n'
                                    '\n'
                                    '# PowerShell, импорт\n'
                                    'python -m app.DBase.import_tasks --source '
                                    "'data/archives/tasks-before-sqlite.json' --database "
                                    "'data/cutover.sqlite3'\n"
                                    '# В новой оболочке Python с оснасткой из теории:\n'
                                    '# inspect_import("data/cutover.sqlite3", '
                                    '"data/archives/tasks-before-sqlite.json")',
                            'checks': ['До импорта видим 0 строк. После импорта видим 2 строки, пустые '
                                       'списки отличающихся ключей и 0 различий в полях.',
                                       'Если значение tags у №34 отличается, отчёт называет №34 и tags. '
                                       'Такая цель остаётся вне переключения.']}},
                {'title': 'Переводим штатные входы на выбранный файл',
                 'task': 'В разделе «Подключаем оба входа и проверяем открытый файл» сравните два '
                         'пункта, которые читали одну заявку из разных баз. Перенесите этот приём на '
                         'Planner. Проследите обычный запуск API в app/api.py и поставщик в '
                         'app/core/dependencies.py. Затем найдите штатный контекст команды в '
                         'app/main.py. Подключите к обоим входам существующие SQL-ресурсы и общий '
                         'DATABASE_URL из app/core/config.py. Сохраните обработчики и границы работы '
                         'Session.\n'
                         '\n'
                         'После изменения запустите обычные чтения через «Смотрим путь в штатной '
                         'Session». Выполните GET /tasks/52, затем CLI find 52. Запишите абсолютный '
                         'адрес выбранной цели и два открытых main. Если хотя бы один путь пуст или '
                         'отличается, поправьте адрес процесса и повторите чтение. До совпадения обоих '
                         'путей запись остаётся закрыта.',
                 'requirements': ['Штатные API и CLI используют существующий SQL-репозиторий и общее '
                                  'значение DATABASE_URL. Для работы с задачами API получает Session на '
                                  'запрос, CLI получает Session на команду.',
                                  'Область записи каждого сервиса и его репозиторий привязаны к той же '
                                  'Session. Закрытие Session сохраняется.',
                                  'Готовая диагностика печатает main из фактически открытого соединения '
                                  'каждого чтения. Оба нормализованных пути равны заранее выбранной цели '
                                  'до первой записи.',
                                  'Обычный GET и CLI find читают №52. Если CLI направлен на зеркало с '
                                  'той же строкой, чтение остаётся успешным, а допуск по открытым путям '
                                  'закрыт.'],
                 'result': 'Два штатных процесса читают №52. Записаны выбранная цель и оба открытых '
                           'пути, которые совпадают с ней.',
                 'hint': 'Сначала отследите, откуда обработчик HTTP получает PlannerService. Для '
                         'отличающегося main проверьте настройки именно того процесса, который его '
                         'напечатал.',
                 'answer': {'explanation': 'Общий адрес нужен в обоих штатных входах. Фабрика Session '
                                           'создаётся на срок жизни процесса, а сервис для данных '
                                           'строится внутри ресурса запроса или команды. Поэтому '
                                           'диагностика того же соединения даёт свидетельство об обычной '
                                           'сборке.',
                            'steps': ['В app/api.py настраиваем Engine по settings.database_url, кладём '
                                      'фабрику Session в app.state и сохраняем освобождение Engine по '
                                      'окончании lifespan. Убираем startup-сборку app.state.planner, '
                                      'которая обслуживала JSON.',
                                      'В app/core/dependencies.py оставляем get_db владельцем короткой '
                                      'Session, а get_planner собирает SQL-сервис для неё. Связь '
                                      'PlannerDep с get_planner сохраняет прежние маршруты. '
                                      'Транзакционная область db.begin относится к этому же репозиторию.',
                                      'В app/main.py после load_settings создаём Engine и фабрику, '
                                      'обычный run получает прежний sql_service_context вместо файлового '
                                      'контекста. Оборачиваем запуск в штатный обработчик StorageError и '
                                      'добавляем освобождение Engine в finally.',
                                      'В обоих терминалах выбираем data/cutover.sqlite3. Через '
                                      'наблюдатель открываем GET /tasks/52 и CLI find 52. При правильной '
                                      'сборке оба main указывают на один абсолютный путь выбранной базы.',
                                      'Для контраста при закрытых ресурсах берём вторую копию базы. Если '
                                      'направить только CLI туда, find всё ещё покажет №52. main выдаст '
                                      'путь зеркала и запись останется закрыта. Вернув исходный адрес, '
                                      'повторяем только два контрольных чтения.'],
                            'code': '# app/api.py · обычная сборка до регистрации маршрутов\n'
                                    'from contextlib import asynccontextmanager\n'
                                    'from app.DBase.session import create_session_factory\n'
                                    'from app.core.engine import make_engine\n'
                                    '\n'
                                    'engine = make_engine(settings.database_url)\n'
                                    '\n'
                                    '@asynccontextmanager\n'
                                    'async def lifespan(_app: FastAPI):\n'
                                    '    try:\n'
                                    '        yield\n'
                                    '    finally:\n'
                                    '        engine.dispose()\n'
                                    '\n'
                                    'app = FastAPI(title=settings.app_name, lifespan=lifespan)\n'
                                    'app.state.settings = settings\n'
                                    'app.state.engine = engine\n'
                                    'app.state.session_factory = create_session_factory(engine)\n'
                                    '# Сохраняем существующие include_router и маршруты.\n'
                                    '\n'
                                    '# app/core/dependencies.py · поставщик сервиса после get_db\n'
                                    'from app.DBase.repositories import SqlTaskRepository\n'
                                    'from app.main import build_service\n'
                                    '\n'
                                    'def get_planner(db: Session = Depends(get_db)) -> PlannerService:\n'
                                    '    return build_service(repository=SqlTaskRepository(db), '
                                    'write_scope=db.begin)\n'
                                    '\n'
                                    '# app/main.py · импорты и изменения внутри обычного main()\n'
                                    'from app.DBase.session import create_session_factory\n'
                                    'from app.core.engine import make_engine\n'
                                    '\n'
                                    '# После settings = load_settings():\n'
                                    '#     engine = make_engine(settings.database_url)\n'
                                    '#     session_factory = create_session_factory(engine)\n'
                                    '# В существующем try:\n'
                                    '#     run(None, context_factory=lambda: '
                                    'sql_service_context(session_factory))\n'
                                    '# После существующего except StorageError:\n'
                                    '#     finally:\n'
                                    '#         engine.dispose()',
                            'checks': ['Обычные GET и find читают №52, main у каждого равен абсолютному '
                                       'пути data/cutover.sqlite3.',
                                       'При зеркале ответ по №52 остаётся тем же, строка main у CLI '
                                       'выдаёт другой путь. После возврата адреса три пути совпадают.']}},
                {'title': 'Читаем новое изменение из другого процесса',
                 'task': 'В разделе «Проверяем новое состояние из другого процесса» проследите номер '
                         'выдачи и прежнее и новое значения. В Planner выберите своё изменение, которое '
                         'другой интерфейс сможет отличить от исходного состояния. Можно завершить '
                         'открытую №34 в CLI или поменять её приоритет через HTTP PATCH.\n'
                         '\n'
                         'Сначала запишите id и старое значение. Выполните изменение на проверенном '
                         'адресе и дождитесь успешного результата. Запустите другой интерфейс в '
                         'отдельном новом процессе, прочитайте тот же id и сравните новое поле. '
                         'Сохраните адрес, id, оба значения и результат независимого чтения. Они '
                         'понадобятся при запуске без архива.',
                 'requirements': ['До записи у выбранной задачи зафиксировано прежнее значение. Штатная '
                                  'операция изменила его на различимое новое.',
                                  'Чтение через второй интерфейс выполнено из отдельного нового процесса '
                                  'на той же SQLite и вернуло изменённое поле у того же id.',
                                  'Исходный архив сохраняет старое значение и остаётся доступен для '
                                  'отдельного импорта.'],
                 'result': 'Другой интерфейс в новом процессе читает тот же id и новое поле. Сохранены '
                           'адрес SQLite, id и пара старое → новое.',
                 'hint': 'Проверьте, что выбранное изменение даёт новое значение. При прежнем поле '
                         'сверьте id, завершение операции и открытый файл читателя.',
                 'answer': {'explanation': 'Успехом здесь служит наблюдение вне процесса, который '
                                           'записал задачу. Выбираем №34, у которой до операции '
                                           'is_done=false. После подтверждения выполняем новый GET и '
                                           'ищем то же id с is_done=true.',
                            'steps': ['Фиксируем №34, is_done=false и адрес data/cutover.sqlite3. '
                                      'Проверяем допуск из предыдущей карточки. Для завершения выбираем '
                                      'CLI done.',
                                      'Обычный CLI получает номер 34, подтверждает завершение задачи и '
                                      'выходит. Прикладная транзакция завершилась до сообщения об '
                                      'успехе.',
                                      'Запускаем новый API-процесс с тем же адресом и вызываем GET '
                                      '/tasks/34. Сопоставляем id и is_done=true. В сохранённом JSON у '
                                      '№34 осталось is_done=false.',
                                      'Если новое чтение оставило False, сравниваем main. Зеркало хранит '
                                      'старое значение. Если адрес верен, проверяем сообщение команды и '
                                      'номер задачи. При альтернативном ходе HTTP PATCH можно поменять '
                                      'приоритет 4 на 5 и найти приоритет 5 в новом CLI.'],
                            'code': '# Терминал CLI (DATABASE_URL указывает на проверенный файл)\n'
                                    'python -m app.main\n'
                                    '# Вводим последовательно: done, 34, exit\n'
                                    '\n'
                                    '# Отдельный новый процесс API с тем же DATABASE_URL\n'
                                    'python -m uvicorn app.api:app --host 127.0.0.1 --port 8765\n'
                                    '# Запрос отдельного клиента\n'
                                    '# GET http://127.0.0.1:8765/tasks/34\n'
                                    '# Ожидаемые поля: "id": 34, "is_done": true',
                            'checks': ['GET №34 из нового API показывает is_done=true после CLI done. '
                                       'Прежний архив показывает is_done=false.',
                                       'При чтении зеркала виден прежний статус. Открытый путь этого '
                                       'процесса указывает на другую SQLite.']}},
                {'title': 'Выводим файловый backend из обычной сборки',
                 'task': 'В разделе «Убираем файловую сборку и проверяем два исхода» проследите, почему '
                         'старое обращение может останавливать новый просмотр и зачем отдельному '
                         'импортёру доступ к старым данным.\n'
                         '\n'
                         'В своей сборке найдите оставшиеся файловые зависимости от API, CLI, Settings и '
                         'PlannerService. Уберите то, что требуется только обычному файловому режиму. '
                         'Сохраните явный импортёр и преобразования объекта Task. Закройте процессы и '
                         'подготовьте отдельную учебную копию с проверенной SQLite после изменения. В '
                         'ней по прежнему адресу нет архивного JSON. Запустите обычные API и CLI и '
                         'прочитайте id из предыдущей карточки с изменённым полем.',
                 'requirements': ['В обычной сборке отсутствуют файловые зависимости при запуске, '
                                  'настройках и операциях с задачами. SQL-ресурсы запросов и команд '
                                  'работают по DATABASE_URL.',
                                  'Прежний внешний договор Planner, поля Task и явный импорт снимка '
                                  'доступны.',
                                  'В отдельной исправной копии старый JSON отсутствует по прежнему '
                                  'адресу. Обычные GET и CLI find читают изменённую задачу из SQLite.'],
                 'result': 'В учебной копии без старого JSON оба интерфейса читают ту же изменённую '
                           'задачу. Исправная копия SQLite сохранена для восстановления.',
                 'hint': 'Ведите две цепочки вызова. Одна начинается в обычном API или CLI, другая в '
                         'явной команде import_tasks. Проверьте, где встречаются старые файловые имена.',
                 'answer': {'explanation': 'В обычной сборке данные уже идут через SQL. Удаляем прежние '
                                           'ветки и их зависимость от JSON_PATH. Импортёр по своей явной '
                                           'команде ещё читает снимок и создаёт записи с помощью '
                                           'существующих преобразований Task.',
                            'steps': ['От API и CLI идём в настройки, поставщика сервиса и репозиторий. '
                                      'Убираем прежний json_service_context, JSON-ветку build_service и '
                                      'вызовы JsonStorage. В API уже есть штатный SQL-поставщик, поэтому '
                                      'у него остаются только SQL-ресурсы.',
                                      'Убираем из настроек DEFAULT_JSON_PATH, поле json_path, разбор '
                                      'JSON_PATH и старое значение из .env.example. В PlannerService '
                                      'оставляем явный repository и область записи. Перед выводом '
                                      'app/storage.py проверяем, кто ещё импортирует его. Явный импортёр '
                                      'берёт Task.from_dict из модели, поэтому этот путь и представление '
                                      'Task сохраняются.',
                                      'Останавливаем соединения и переносим подтверждённую учебную '
                                      'SQLite в новую нормальную копию без data/tasks.json. Проверяем её '
                                      'адрес в обоих терминалах. Находясь в этой копии, запускаем '
                                      'обычный GET и CLI find для №34.',
                                      'В примере обе точки читают «Купить бумагу» со статусом выполнено. '
                                      'Если запуск запрашивает JSON, возвращаемся к указанному в ошибке '
                                      'месту и ищем его входящую связь из обычного запуска.'],
                            'code': '# app/main.py · сборка принимает готовый репозиторий\n'
                                    'def build_service(*, repository, write_scope=None):\n'
                                    '    return PlannerService(repository=repository, '
                                    'write_scope=write_scope)\n'
                                    '\n'
                                    '# app/service.py · начало конструктора\n'
                                    'def __init__(self, *, repository, write_scope=None):\n'
                                    '    self.repository = repository\n'
                                    '    self.write_scope = nullcontext if write_scope is None else '
                                    'write_scope\n'
                                    '\n'
                                    '# В учебной нормальной копии: PowerShell\n'
                                    "Test-Path -LiteralPath 'data/tasks.json'\n"
                                    "$env:DATABASE_URL = 'sqlite:///data/cutover.sqlite3'\n"
                                    'python -m app.main\n'
                                    '# find, 34, exit. В другом терминале обычный API с тем же адресом\n'
                                    '# GET http://127.0.0.1:8765/tasks/34',
                            'checks': ['Test-Path в нормальной копии показывает False. CLI find и '
                                       'обычный GET читают №34 с изменённым полем.',
                                       'Путь явного импорта сохраняет собственный источник --source и '
                                       'использует модель Task для чтения данных.']}},
                {'title': 'Проверяем отказ при читаемом старом архиве',
                 'task': 'В разделе «Убираем файловую сборку и проверяем два исхода» найдите случай с '
                         'недоступным журналом и контрольной старой выдачей. Для Planner подготовьте '
                         'другую учебную копию. Выберите для DATABASE_URL адрес, по которому вместо '
                         'SQLite-файла находится каталог. По прежнему пути data/tasks.json поместите '
                         'валидную задачу №901 «АРХИВНЫЙ МАРКЕР», приоритет 2, is_done=false, tags=[]. '
                         'Убедитесь, что JSON читается и содержит этот маркер.\n'
                         '\n'
                         'Запросите №901 через обычный GET и CLI find. Зафиксируйте исход операции и '
                         'появилось ли архивное значение. Затем верните исправный адрес и контрольным '
                         'GET прочитайте сохранённую задачу. Отдельно повторите знакомый явный импорт '
                         'исходного архива в новую пустую цель, чтобы подтвердить этот оставшийся способ '
                         'переноса.',
                 'requirements': ['Отказной опыт идёт в другом каталоге. Активный DATABASE_URL указывает '
                                  'на каталог вместо файла, а старый JSON-путь содержит читаемую задачу '
                                  '№901.',
                                  'Обычный GET и CLI find дают отказ операции без успешной выдачи №901 '
                                  'или её названия.',
                                  'После возврата адреса обычный GET читает подтверждённую задачу. '
                                  'Исходный архив импортируется явной командой в отдельную пустую цель.'],
                 'result': 'Есть наблюдения двух отказов при читаемом маркере, успешного GET после '
                           'восстановления и отдельного импорта исходного архива.',
                 'hint': 'Сначала убедитесь, что старый путь действительно даёт различимую строку. '
                         'Успешный ответ с id 901 укажет, где искать оставшийся файловый вызов.',
                 'answer': {'explanation': 'Нормальный запуск без JSON показал, что архив не нужен при '
                                           'исправной SQLite. В отказной копии предоставляем читаемый '
                                           'старый JSON, чтобы возможный переход к нему был виден по '
                                           '№901. Ожидаем отказ чтения задачи в обоих штатных входах.',
                            'steps': ['В отдельной учебной копии создаём каталог data/sqlite-unavailable '
                                      'и направляем DATABASE_URL на него. По прежнему пути '
                                      'data/tasks.json размещаем одну синтетическую задачу с номером 901 '
                                      'и заметным названием.',
                                      'Проверяем файл локальным JSON-читателем: видим №901 и «АРХИВНЫЙ '
                                      'МАРКЕР». Если файл пуст или адрес отличается от прежнего, '
                                      'проверка не различит скрытый файловый путь.',
                                      'Запрашиваем /tasks/901 и CLI find 901. Операции сообщают об '
                                      'ошибке хранилища и не возвращают запись. Если видим №901 с '
                                      'маркером, прослеживаем оставшуюся ветку к старому JSON.',
                                      'Закрываем отказные процессы. Возвращаем DATABASE_URL исправной '
                                      'копии и проверяем GET /tasks/34. Для явного импорта готовим ещё '
                                      'одну пустую SQLite, указываем исходный архив как --source, '
                                      'сверяем исходные значения задач.'],
                            'code': '# PowerShell, отдельная учебная отказная копия\n'
                                    "New-Item -ItemType Directory -Path 'data/sqlite-unavailable'\n"
                                    "$env:DATABASE_URL = 'sqlite:///data/sqlite-unavailable'\n"
                                    "$env:JSON_PATH = 'data/tasks.json'\n"
                                    '\'[{"id":901,"title":"АРХИВНЫЙ '
                                    'МАРКЕР","priority":2,"is_done":false,"tags":[]}]\' |\n'
                                    "  Set-Content -LiteralPath 'data/tasks.json' -Encoding utf8NoBOM\n"
                                    "python -m json.tool 'data/tasks.json' --no-ensure-ascii\n"
                                    '# Штатный HTTP: GET http://127.0.0.1:8765/tasks/901\n'
                                    '# Штатный CLI: find, 901 или явное сообщение о невозможности '
                                    'открыть базу\n'
                                    '\n'
                                    '# Явный импорт, из учебной копии с допущенным архивом\n'
                                    "$env:DATABASE_URL = 'sqlite:///data/import-only.sqlite3'\n"
                                    'python -m app.DBase.init_db\n'
                                    'python -m app.DBase.import_tasks --source '
                                    "'data/archives/tasks-before-sqlite.json' --database "
                                    "'data/import-only.sqlite3'",
                            'checks': ['Локальное JSON-чтение показывает маркер. Обычные GET и CLI find '
                                       'не выдают его и дают отказ чтения задачи.',
                                       'GET на исправном адресе возвращает №34. Сверка отдельного '
                                       'импорта даёт исходные значения из архива, включая прежний статус '
                                       '№34.']}}],
      'result': 'Обычные API и CLI работают с одной SQLite и независимо читают новое изменение. '
                'Проверены точный архив, полный импорт, открытые адреса, два исхода без файловой сборки '
                'и отдельный явный импорт.'}],
    "74.1": [{'title': 'Репетируем перенос Planner',
              'task': 'Продолжите свой Planner с явным init, отдельным импортёром, пробным '
                      'HTTP-provider и SQL-контекстом CLI. Работайте из корня проекта в PowerShell. '
                      'Используйте отдельные процессы Python, новый каталог data/rehearsal и только '
                      'синтетические значения. Все команды, адреса и наблюдения записывайте в '
                      'observations.md внутри выбранного каталога.',
              'tasks': [{'title': 'Подтвердить отдельную пустую цель',
                         'task': 'Вернитесь к «Источник, адрес и пустая цель». Как в реестре, сначала '
                                 'выберите два разных файловых пути. В новом каталоге '
                                 'data/rehearsal/<ваше имя> создайте import-source.json с двумя '
                                 'записями. У задачи 17 заголовок «Сверить СКЛАД», приоритет 3, '
                                 'is_done равен false, tags содержит «склад». У задачи 24 заголовок '
                                 '«Убрать коробки», приоритет 1, is_done равен true, ключ tags '
                                 'отсутствует. В routine.json возьмите те же записи и добавьте id 8 '
                                 '«Обычная отметка», приоритет 2, is_done равен false. Сохраните '
                                 'отдельную побайтную копию каждого JSON рядом с оригиналом.\n'
                                 '\n'
                                 'Выберите свободное имя SQLite внутри того же каталога. Запишите '
                                 'абсолютные пути к снимку, обычному JSON и цели. В каждом пробном '
                                 'терминале до старта Python явно укажите JSON_PATH на этот '
                                 'routine.json, а DATABASE_URL на выбранную учебную SQLite. В разделе '
                                 '«Возврат к обычному JSON» найдите два исходных наблюдения: запустите '
                                 'обычные HTTP-клиент и CLI, прочитайте в обоих id 17, запишите '
                                 'параметры и завершите эти запуски.\n'
                                 '\n'
                                 'Вызовите изученный init с выбранным URL. С помощью готовой проверки '
                                 'из раздела «Источник, адрес и пустая цель» получите имя таблицы и '
                                 'число строк. Остановитесь, если путь занят, таблица отсутствует или '
                                 'в ней уже есть записи. Результат сохраните до импорта.',
                         'requirements': ['В новом учебном каталоге есть два исходных JSON, их '
                                          'контрольные копии и observations.md.',
                                          'Обычные API и CLI в этом прогоне читают синтетический '
                                          'routine.json и находят контрольный id 17.',
                                          'В записи прогона указаны исходные параметры и разные '
                                          'абсолютные пути к import-source.json и новой SQLite; её URL '
                                          'ведёт в учебный каталог.',
                                          'После явного init готовая проверка выбранного файла '
                                          'показывает tasks и 0 строк.'],
                         'result': 'Зафиксированы исходный режим, читаемый снимок и отдельная цель с '
                                   'пустой tasks. Теперь можно составить команду импорта.',
                         'hint': 'Отдельно назовите файл, который читает импортёр, и тот, в котором '
                                 'init должен создать таблицу. Какое из двух показаний означает '
                                 '«таблица существует»?',
                         'answer': {'explanation': 'Обычный запуск сначала проверяем на известном '
                                                   'JSON. Затем создаём отдельную SQLite. Файл с '
                                                   'пустой таблицей годится для переноса, а отсутствие '
                                                   'таблицы или занятое имя требует остановки.',
                                    'steps': ['Выбираем свободный каталог, например '
                                              'data/rehearsal/proba-a. В редакторе сохраняем '
                                              'import-source.json с id 17 и 24 и routine.json с id 8, '
                                              '17 и 24. Снимаем копии до запуска, чтобы потом сравнить '
                                              'исходные байты.',
                                              'Выбираем ещё отсутствующую trial-a.sqlite3. Записываем '
                                              'абсолютный путь и строим URL из того же пути. Явный '
                                              'JSON_PATH привязывает обычные процессы к учебным '
                                              'файлам, даже если в проекте есть другие настройки.',
                                              'Из корня проекта после этих настроек открываем Python и '
                                              'TestClient(app) без SQL-подмены. GET /tasks/17 даёт 200 '
                                              'и «Сверить СКЛАД». В отдельном терминале с теми же '
                                              'путями python -m app.main и find 17 показывают эту же '
                                              'задачу. В observations.md записываем оба запуска и '
                                              'показания, затем закрываем клиент и выходим из Python и '
                                              'CLI.',
                                              'Снова проверяем свободный путь, выполняем init и '
                                              'запускаем готовый инспектор с тем же $target. Имя tasks '
                                              'и 0 дают два отдельных подтверждения. Если файл уже '
                                              'был, выбираем другое имя; если после init таблица '
                                              'отсутствует, сверяем URL init и путь инспектора.'],
                                    'code': '# В PowerShell из корня Planner, после выбора свободного '
                                            'имени каталога.\n'
                                            '$runDir = Join-Path (Get-Location).Path '
                                            "'data/rehearsal/proba-a'\n"
                                            "if (Test-Path -LiteralPath $runDir) { throw 'Выберите "
                                            "свободное имя' }\n"
                                            'New-Item -ItemType Directory -Path $runDir -ErrorAction '
                                            'Stop\n'
                                            '\n'
                                            '# Два JSON и observations.md создаём в редакторе по '
                                            'данным условия.\n'
                                            "$source = Join-Path $runDir 'import-source.json'\n"
                                            "$routine = Join-Path $runDir 'routine.json'\n"
                                            "$target = Join-Path $runDir 'trial-a.sqlite3'\n"
                                            'Copy-Item -LiteralPath $source -Destination '
                                            '"$source.before"\n'
                                            'Copy-Item -LiteralPath $routine -Destination '
                                            '"$routine.before"\n'
                                            '$env:JSON_PATH = $routine\n'
                                            "$env:DATABASE_URL = 'sqlite:///' + ($target -replace "
                                            "'\\\\', '/')\n"
                                            '\n'
                                            '# Обычный API: python, затем в Python.\n'
                                            'from app.api import app\n'
                                            'from fastapi.testclient import TestClient\n'
                                            'client = TestClient(app)\n'
                                            'response = client.get("/tasks/17")\n'
                                            'print(response.status_code, response.json()["title"])\n'
                                            'client.close()\n'
                                            'exit()\n'
                                            '\n'
                                            '# Обычный CLI: PowerShell с теми же переменными.\n'
                                            'python -m app.main\n'
                                            '# В CLI: find, 17, exit.\n'
                                            '\n'
                                            '# В PowerShell, когда оба обычных запуска завершены.\n'
                                            "if (Test-Path -LiteralPath $target) { throw 'Выберите "
                                            "свободную SQLite' }\n"
                                            'python -m app.DBase.init_db\n'
                                            '# Загружаем $inspect из раздела о пустой цели.\n'
                                            'python -c $inspect "$target"',
                                    'checks': ['Первый HTTP вызов показывает 200 и «Сверить СКЛАД»; '
                                               'find 17 в обычном CLI показывает ту же задачу.',
                                               'Инспектор печатает полный путь к выбранной '
                                               'trial-a.sqlite3 и tasks 0. При выборе чужого или '
                                               'существующего файла этот путь отбрасывается до '
                                               'init.']}},
                        {'title': 'Сверить перенос и принять решение',
                         'task': 'В «Источник, адрес и пустая цель» найдите, какой аргумент принимает '
                                 'путь к снимку, а какой путь к базе. Возьмите свои абсолютные пути из '
                                 'observations.md, составьте вызов готового импортёра и запустите его '
                                 'на подтверждённой пустой цели.\n'
                                 '\n'
                                 'Теперь откройте «Сверка по id перед первой записью». Примените то же '
                                 'сопоставление по номеру и значениям: запустите готовый компаратор '
                                 'для перенесённых данных и запишите, какие id и поля совпали. По '
                                 'этому отчёту решите, допустима ли новая запись. При несовпадении '
                                 'зафиксируйте первое различие и проверьте адреса, прежде чем '
                                 'повторять на новой пустой цели.\n'
                                 '\n'
                                 'Чтобы проверить, что решение зависит от полей, создайте отдельную '
                                 'копию снимка tags-variant.json. Только у id 17 измените tags на '
                                 'пустой список. Сравните эту копию с уже импортированной SQLite. '
                                 'Сформулируйте отдельное решение для этой пары. Источник импорта, '
                                 'SQLite и обычный JSON в этой части не редактируются.',
                         'requirements': ['Импортёр получил отдельные файловые пути к снимку и '
                                          'выбранной пустой SQLite.',
                                          'В observations.md есть решение по набору id и сравнению id, '
                                          'title, priority, is_done, tags, title_normalized до пробных '
                                          'записей.',
                                          'Для tags-variant.json совпавшее число записей сочетается с '
                                          'одним отличием по tags у id 17. Вывод о продолжении основан '
                                          'на этом отличии.'],
                         'result': 'Полное совпадение проверенной пары даёт допуск к пробной записи. '
                                   'Для изменённой пары отдельно объяснено, почему счётчики совпали и '
                                   'где нужно остановиться.',
                         'hint': 'Посмотрите, какое значение ожидается для записи без ключа tags, а '
                                 'какое для записи с потерянной меткой. Строки отчёта привязаны к id.',
                         'answer': {'explanation': 'Импорт и сверка дают разные наблюдения. Успешный '
                                                   'импорт сообщает число перенесённых записей. '
                                                   'Компаратор проверяет состав и значения, поэтому '
                                                   'улавливает потерю тега при том же числе строк.',
                                    'steps': ['Подставляем в флаги пути из карточки с пустой целью. '
                                              'Передаём trial-a.sqlite3 как файл. Вариант с sqlite:/// '
                                              'в --database не пройдёт проверку существующего файла.',
                                              'Читаем отчёт для исходного снимка. Оба счётчика равны '
                                              '2, оба списка исключений пусты. У 17 сохраняются '
                                              'название, приоритет 3, False и [«склад»]; служебное '
                                              'название равно «сверить склад». У 24 сохраняются '
                                              'название, приоритет 1 и True, а пропущенный tags даёт '
                                              '[]. title_normalized равен «убрать коробки». Две строки '
                                              'полного совпадения подтверждают допуск.',
                                              'Создаём tags-variant.json из снимка и только там '
                                              'убираем метку у 17. Сравнение на той же SQLite '
                                              'показывает 2/2, но у 17 ожидаемое значение [] '
                                              'расходится с фактическим [«склад»]. По этой паре '
                                              'решение «остановиться». Исходный прогон по '
                                              'подтверждённой паре остаётся пригодным.',
                                              'При таком же расхождении в действительном источнике '
                                              'сначала сверяем выбранные пути и ожидаемые значения. '
                                              'После устранения причины импортируем в новую пустую '
                                              'цель и вновь сравниваем перед POST.'],
                                    'code': '# PowerShell, переменные из карточки с пустой целью.\n'
                                            'python -m app.DBase.import_tasks --source "$source" '
                                            '--database "$target"\n'
                                            '# Определяем $compare из раздела «Сверка по id перед '
                                            'первой записью».\n'
                                            'python -c $compare "$source" "$target"\n'
                                            '\n'
                                            "$variant = Join-Path $runDir 'tags-variant.json'\n"
                                            'Copy-Item -LiteralPath $source -Destination $variant\n'
                                            '# В редакторе изменяем только tags записи 17 на [] в '
                                            '$variant.\n'
                                            'python -c $compare "$variant" "$target"',
                                    'checks': ['В исходной паре видны JSON / SQLite 2 2, пустые списки '
                                               'лишних и пропавших id и полное совпадение 17 и 24.',
                                               'В изменённой паре видна строка id 17 tags [] != '
                                               "['склад'] при 2 2. До записи существует обоснованное "
                                               'решение по каждой паре.']}},
                        {'title': 'Прочитать новый id из другой точки',
                         'task': 'Продолжайте по совпавшей паре из сверки. В «Источник, адрес и пустая '
                                 'цель» сверьте форму URL. В «Одна база и новое чтение по id» '
                                 'проследите, откуда берётся новый номер и как его читает отдельное '
                                 'соединение. Соберите свои два пробных запуска из прежнего '
                                 'HTTP-provider с get_planner и контекста sql_service_context для CLI. '
                                 'В обоих терминалах примените один проверенный DATABASE_URL и прежний '
                                 'синтетический JSON_PATH. Для API используйте изученный TestClient с '
                                 'SQL-подменой; CLI поднимите в другом Python-процессе с контекстом на '
                                 'команду.\n'
                                 '\n'
                                 'Напишите пробный POST /tasks с выбранным названием и допустимым '
                                 'приоритетом. Сохраните статус, id и название из ответа в '
                                 'observations.md. В CLI выполните find по этому id. Готовым чтением '
                                 'из раздела «Возврат к обычному JSON» проверьте, что номера нет в '
                                 'import-source.json и routine.json. Если номер уже есть в прежних '
                                 'данных, получите отличимый номер ещё одной пробной записью на этой '
                                 'же проверенной SQLite и повторите чтение. При пропаже записи в CLI '
                                 'сначала сопоставьте открытые SQL-файлы и id ответа.',
                         'requirements': ['В обеих пробных сборках указан URL подтверждённой SQLite. '
                                          'API и CLI используют отдельные процессы и короткие Session.',
                                          'POST вернул 201, в observations.md есть фактический id и '
                                          'заголовок новой записи.',
                                          'Новая команда find из SQL CLI возвращает тот же id и '
                                          'заголовок; оба исходных JSON не содержат этот id.'],
                         'result': 'Новая запись читается через другой интерфейс из той же SQLite, а '
                                   'её id различает пробное и прежнее содержимое.',
                         'hint': 'Откуда берётся число для find? Старая запись может найтись и в '
                                 'другой копии. Сравните открытые пути, если в CLI не видно нового '
                                 'номера.',
                         'answer': {'explanation': 'API подтверждает транзакцию до успешного ответа. '
                                                   'CLI открывает отдельную короткую Session на '
                                                   'распознанную команду. Новая запись в другом '
                                                   'процессе подтверждает связь обоих запусков через '
                                                   'один файл.',
                                    'steps': ['В двух терминалах выставляем выбранный URL из '
                                              'observations.md и прежний JSON_PATH. Для каждого движка '
                                              'отдельно читаем PRAGMA database_list и сравниваем путь '
                                              'main с выбранной целью. Проверяем фактически открытый '
                                              'файл, так как одна и та же старая задача бывает в двух '
                                              'копиях.',
                                              'В первом Python-процессе берём свой готовый provider из '
                                              'проверки ресурса HTTP. Он создаёт PlannerService на '
                                              'SqlTaskRepository(db), передавая db.begin как границу '
                                              'записи. Привязываем фабрику к app.state и provider к '
                                              'get_planner. Выполняем POST с допустимыми значениями и '
                                              'записываем id из ответа. В примере это 25.',
                                              'Во втором Python-процессе создаём Engine и фабрику на '
                                              'том же URL. Передаём run вызываемый контекст '
                                              'sql_service_context, а не уже открытый ресурс. Вводим '
                                              'find и номер 25 из ответа. Новое чтение показывает 25 и '
                                              'выбранный заголовок.',
                                              'Дважды запускаем read-only проверку JSON, для каждого '
                                              'JSON берём его собственную контрольную копию. Искомый '
                                              'id отсутствует. Если CLI показывает старую задачу, но '
                                              'пропускает новую, сравниваем открытые пути, затем число '
                                              'из ответа и состояние записи. Сохраняем наблюдения до '
                                              'завершения пробных процессов.'],
                                    'code': '# Пробный API. Сначала в PowerShell заданы учебные пути, '
                                            'затем python.\n'
                                            'from fastapi import Depends\n'
                                            'from fastapi.testclient import TestClient\n'
                                            'from sqlalchemy.orm import Session, sessionmaker\n'
                                            'from app.api import app\n'
                                            'from app.core.config import load_settings\n'
                                            'from app.core.dependencies import get_db, get_planner\n'
                                            'from app.core.engine import make_engine\n'
                                            'from app.DBase.repositories import SqlTaskRepository\n'
                                            'from app.main import build_service\n'
                                            '\n'
                                            'engine = make_engine(load_settings().database_url)\n'
                                            'with engine.connect() as connection:\n'
                                            '    print(connection.exec_driver_sql("PRAGMA '
                                            'database_list").all())\n'
                                            '\n'
                                            'def rehearsal_provider(db: Session = Depends(get_db)):\n'
                                            '    return '
                                            'build_service(repository=SqlTaskRepository(db), '
                                            'write_scope=db.begin)\n'
                                            '\n'
                                            'app.state.session_factory = sessionmaker(bind=engine)\n'
                                            'app.dependency_overrides[get_planner] = '
                                            'rehearsal_provider\n'
                                            'client = TestClient(app)\n'
                                            'response = client.post("/tasks", json={"title": "Пробная '
                                            'белая метка", "priority": 2})\n'
                                            'print(response.status_code, response.json())\n'
                                            '# В примере id 25. Переносим видимый id из своего '
                                            'ответа.\n'
                                            '\n'
                                            '# В другом Python с теми же явно заданными учебными '
                                            'путями.\n'
                                            'from sqlalchemy.orm import sessionmaker\n'
                                            'from app.cli import run\n'
                                            'from app.core.config import load_settings\n'
                                            'from app.core.engine import make_engine\n'
                                            'from app.main import sql_service_context\n'
                                            '\n'
                                            'engine = make_engine(load_settings().database_url)\n'
                                            'with engine.connect() as connection:\n'
                                            '    print(connection.exec_driver_sql("PRAGMA '
                                            'database_list").all())\n'
                                            '\n'
                                            'factory = sessionmaker(bind=engine)\n'
                                            'run(context_factory=lambda: '
                                            'sql_service_context(factory))\n'
                                            '# В CLI: find, фактический id, затем exit для выхода из '
                                            'цикла.\n'
                                            '\n'
                                            '# В PowerShell после определения $readJson из теории.\n'
                                            '$number = 25\n'
                                            'python -c $readJson "$source" "$number" "$source.before"\n'
                                            'python -c $readJson "$routine" "$number" '
                                            '"$routine.before"',
                                    'checks': ['POST даёт 201 и id 25 в показанном прогоне. find 25 '
                                               'возвращает «Пробная белая метка» на выбранной цели.',
                                               'На другой SQLite со старым id 17 не находится id 25. '
                                               'На обоих учебных JSON команда читает «id присутствует: '
                                               'False».']}},
                        {'title': 'Восстановить обычные запуски',
                         'task': 'В «Возврат к обычному JSON» сопоставьте обычную ведомость и пробную '
                                 'картотеку. Откройте observations.md. Возьмите начальные параметры '
                                 'обеих точек, старый id 17 и новый id из перекрёстного чтения. '
                                 'Завершите пробный TestClient и оба пробных Python-процесса. Вновь '
                                 'запустите обычный HTTP-клиент и обычный CLI с записанным учебным '
                                 'JSON_PATH и без SQL-подмены.\n'
                                 '\n'
                                 'В обоих интерфейсах прочитайте контрольный id и пробный id. '
                                 'Сохраните каждый результат. Сравните оба исходных JSON со своими '
                                 'копиями до запуска готовой read-only проверкой из той же теории. '
                                 'Если один интерфейс показывает пробный id или пропускает старый, '
                                 'проверьте его активный режим и адрес и исправьте запуск перед '
                                 'итоговым выводом.',
                         'requirements': ['Обычный HTTP-клиент и обычный CLI запущены заново с '
                                          'синтетическим routine.json.',
                                          'У API для контрольного id записан 200, для пробного 404. '
                                          'CLI находит контрольную задачу и сообщает об отсутствии '
                                          'пробной.',
                                          'Оба исходных JSON побайтно совпадают каждый со своей '
                                          'контрольной копией, вывод по четырём чтениям записан в '
                                          'observations.md.'],
                         'result': 'Обе обычные точки вернулись к синтетическому JSON, который '
                                   'использовался на старте. Пробный id остался отличимым, а байты '
                                   'обоих исходных JSON сохранились.',
                         'hint': 'Сопоставьте два номера в каждой точке отдельно. Как изменится пара '
                                 'результатов, если одна из точек продолжает читать SQLite?',
                         'answer': {'explanation': 'Старый номер можно найти в обоих режимах, потому '
                                                   'что он переехал при импорте. Наличие старого '
                                                   'номера вместе с отсутствием нового подтверждает '
                                                   'возврат. Сверка байтов отвечает на отдельный '
                                                   'вопрос о сохранности файлов.',
                                    'steps': ['Сначала закрываем клиент и пробные процессы. Только '
                                              'новые процессы получат исходные параметры. В PowerShell '
                                              'оставляем привязку JSON_PATH к синтетическому '
                                              'routine.json и возвращаем режим сборки без временного '
                                              'provider и без SQL-контекста CLI.',
                                              'В обычном API выполняем GET /tasks/17 и GET /tasks/25. '
                                              'В примере получаем 200 для прежней задачи и 404 для '
                                              'пробной. В обычном CLI выполняем find для этих же двух '
                                              'id и получаем найденную прежнюю задачу и сообщение об '
                                              'отсутствии пробной.',
                                              'Если CLI находит 25, проверяем, какой context_factory '
                                              'был передан: процесс мог остаться пробным. Если обычная '
                                              'точка не находит даже 17, проверяем, использовала ли '
                                              'она routine.json, а не другой пустой JSON. Снова '
                                              'запускаем именно эту точку и повторяем два чтения.',
                                              'Для исходного и обычного JSON используем свои '
                                              'контрольные копии из первой карточки. Два True в '
                                              'сравнении байтов подтверждают, что обычный JSON и '
                                              'снимок не изменились. Записываем результаты и завершаем '
                                              'прогоны.'],
                                    'code': '# В пробном API: client.close(), engine.dispose(), '
                                            'exit().\n'
                                            '# В пробном CLI: exit, затем в Python engine.dispose(), '
                                            'exit().\n'
                                            '\n'
                                            '# В PowerShell используем записанные исходные настройки '
                                            'учебного прогона.\n'
                                            '$env:JSON_PATH = $routine\n'
                                            '# Запускаем python из корня проекта. Обычный provider '
                                            'используется при новом импорте.\n'
                                            'from fastapi.testclient import TestClient\n'
                                            'from app.api import app\n'
                                            '\n'
                                            'client = TestClient(app)\n'
                                            'print(client.get("/tasks/17").status_code, '
                                            'client.get("/tasks/25").status_code)\n'
                                            'client.close()\n'
                                            'exit()\n'
                                            '\n'
                                            '# PowerShell для обычного CLI с тем же JSON_PATH.\n'
                                            'python -m app.main\n'
                                            '# В CLI: find, 17, find, 25, exit.\n'
                                            '\n'
                                            '# PowerShell с готовым $readJson.\n'
                                            '$number = 25\n'
                                            'python -c $readJson "$source" "$number" "$source.before"\n'
                                            'python -c $readJson "$routine" "$number" '
                                            '"$routine.before"',
                                    'checks': ['Обычный API показывает 200 404, CLI находит 17 и '
                                               'сообщает об отсутствии 25.',
                                               'Побайтное сравнение для каждого из двух файлов '
                                               'возвращает True. В observations.md лежат четыре '
                                               'результата чтения, адрес и вывод.']}}],
              'result': 'Пустая цель, полная сверка, чтение одного нового id из двух процессов и '
                        'возврат обычных точек подтверждены на синтетических данных.'}],
    73: [
        {'title': 'Настраиваем список и поиск в SQL-репозитории',
         'task': 'Продолжим собственный Planner. В SQL-репозитории уже работает list(). В конце теории '
                 'откроем «Проверка в одной сессии Python» и подготовим пять учебных строк. Все четыре '
                 'карточки используют ту же базу в памяти. После каждой правки файла выполним `repo = '
                 'reload(repositories).SqlTaskRepository(lab_session)` в той же сессии Python. Так '
                 'проверка вызывает ваш сохранённый метод. После последней карточки закроем сессию по '
                 'инструкции в теории.',
         'tasks': [{'title': 'В выборке остаются задачи нужного статуса',
                    'task': 'Из одной таблицы нам нужны то все задачи, то только открытые или '
                            'завершённые. В `app/DBase/repositories.py` добавим в `SqlTaskRepository` '
                            'метод `select(self, is_done=None, sort_desc=False, limit=None)`. Пока '
                            'используем обычный порядок по id и вызываем метод без настроек порядка и '
                            'предела. Вернёмся к разделу «Как условие оставляет нужные строки». Возьмём '
                            'оттуда приём, который учитывает явное значение False и пропускает фильтр '
                            'для None. В `list()` уже показано, как из ORM-строк получить объекты Task.\n'
                            '\n'
                            'После сохранения проверим три вызова в Python. Сначала выполним '
                            '`print([task.id for task in repo.select(is_done=False)])`, затем такой же '
                            'вызов с `is_done=True` и без аргумента. Для первых двух ответов найдём '
                            'условие по `is_done` в выполненном SELECT. Для вызова без аргумента сравним '
                            'результат со всем исходным списком.',
                    'requirements': ['В `SqlTaskRepository` определён `select(self, is_done=None, '
                                     'sort_desc=False, limit=None)`.',
                                     '`False` и `True` ограничивают набор по статусу внутри SQLite. Для '
                                     '`None` фильтр по статусу отсутствует.',
                                     'Метод возвращает `list[Task]` в порядке возрастания id для вызовов '
                                     'с остальными параметрами по умолчанию.'],
                    'result': 'Для False видны [27, 35, 53], для True видны [12, 42], для None видны '
                              '[12, 27, 35, 42, 53]. В первом и втором SELECT есть условие по статусу.',
                    'hint': 'Начнём с первой лишней строки в выводе. Что передал вызывающий код и какую '
                            'отметку SQLite должна была сравнить у этой строки?',
                    'answer': {'explanation': 'Значение False означает запрос открытых задач. None '
                                              'означает, что статус выбирать не надо. Сначала принимаем '
                                              'это решение в Python, затем передаём сравнение колонки '
                                              'SQLite.',
                               'steps': ['Начните в существующем классе с запроса ORM-строк TaskRow. '
                                         'База вернёт эти строки, а код преобразует их в Task с помощью '
                                         'того же приёма, что и в list().',
                                         'Вызов с is_done=False идёт в ветку заданного фильтра. У строки '
                                         '35 отметка False, поэтому она проходит. У строки 42 отметка '
                                         'True, поэтому она пропускается. В этой ветке сохраните новый '
                                         'запрос, который вернул where.',
                                         'Пока оставьте обычный порядок по id. Выполните уже собранный '
                                         'запрос через Session и переведите каждую строку в Task.',
                                         'При `if is_done` запрос открытых ошибочно пройдёт мимо WHERE. '
                                         'В ответе окажется лишняя 12, а напечатанный SELECT будет похож '
                                         'на вариант без фильтра. Сверьте оба статуса и вызов без '
                                         'статуса.'],
                               'code': 'if is_done is not None:\n'
                                       '    statement = statement.where(TaskRow.is_done == is_done)',
                               'checks': ['`[task.id for task in repo.select(is_done=False)]` даёт [27, '
                                          '35, 53].',
                                          'С `True` получаем [12, 42]. Без фильтра получаем все пять id '
                                          'и видим SELECT без условия по статусу.']}},
                   {'title': 'Предел берётся из подходящих задач в заданном порядке',
                    'task': 'Разовьём метод `select` из предыдущей карточки. Если просят две открытые '
                            'задачи с большими id, SQLite должна сначала учесть статус и порядок и '
                            'только потом вернуть первые две. В разделе «Порядок строк и предел выдачи» '
                            'сравним оба направления и посмотрим, в какой момент действует '
                            'необязательный предел. Дополним `select` в `app/DBase/repositories.py` так, '
                            'чтобы вызывающий код мог менять оба параметра независимо.\n'
                            '\n'
                            'После сохранения проверим `print([task.id for task in '
                            'repo.select(is_done=False, limit=2)])`, затем тот же вызов с '
                            '`sort_desc=True`. Уберём `limit=2` в обоих вариантах и сравним полный '
                            'порядок. Найдём `ORDER BY` и `LIMIT` в выполненных запросах.',
                    'requirements': ['В `select` параметр `sort_desc` управляет направлением '
                                     'SQL-сортировки по id. По умолчанию порядок возрастающий.',
                                     'При заданном `limit` SQLite возвращает первые строки подходящего '
                                     'упорядоченного набора. При `limit=None` возвращается весь '
                                     'подходящий набор.',
                                     'Отбор по статусу выполняется в том же SELECT. Выполненный запрос '
                                     'содержит `LIMIT` только при заданном пределе.'],
                    'result': 'С пределом 2 видны [27, 35] по возрастанию и [53, 35] по убыванию. Без '
                              'предела видны [27, 35, 53] и [53, 35, 27]. SQL-след подтверждает '
                              'направление и предел.',
                    'hint': 'Сначала выпишем весь открытый набор в нужном порядке. Какие две строки '
                            'находятся в его начале и что сообщит об этом SQL-след?',
                    'answer': {'explanation': 'Статус определяет подходящие строки, порядок расставляет '
                                              'их по id, предел берёт начало результата. Все три '
                                              'свойства проверяем на выполненном SELECT.',
                               'steps': ['Сохраните статусную ветку предыдущей карточки. При '
                                         'is_done=False до ограничения подходят 27, 35 и 53.',
                                         'Выберите направление по sort_desc. В случае True порядок до '
                                         'предела будет [53, 35, 27]. Добавьте это направление к тому же '
                                         'запросу.',
                                         'Если передали предел 2, ограничьте запрос до его выполнения. '
                                         'SQLite вернёт 53 и 35 в этом порядке. При None выполните '
                                         'упорядоченный запрос без ограничения.',
                                         'Если сперва взять два максимальных id среди всех, вторым '
                                         'окажется закрытая 42. После фильтра останется лишь 53. Срез в '
                                         'Python может дать верные номера, но в выполненном SELECT не '
                                         'будет LIMIT.'],
                               'code': 'direction = TaskRow.id.desc() if sort_desc else '
                                       'TaskRow.id.asc()\n'
                                       'statement = statement.order_by(direction)\n'
                                       'if limit is not None:\n'
                                       '    statement = statement.limit(limit)',
                               'checks': ['`[task.id for task in repo.select(is_done=False, '
                                          'sort_desc=True, limit=2)]` даёт [53, 35].',
                                          'Сверьте [27, 35] для возрастания и три открытых id в обоих '
                                          'направлениях без предела. У последнего варианта нет LIMIT.']}},
                   {'title': 'Поисковый ввод готов до первого SELECT',
                    'task': 'Записки «  ОТЧЁТ 10%  » и «отчёт 10%» содержат один и тот же поисковый '
                            'фрагмент. Записку из одних пробелов искать не будем. В '
                            '`app/DBase/repositories.py` начнём метод `search(self, query)` в '
                            '`SqlTaskRepository`. В разделе «Подготовка строки поиска» вспомним порядок '
                            'строковых действий и ранний возврат для пустой строки. Подготовленный '
                            'фрагмент сохраним для следующей карточки.\n'
                            '\n'
                            'Чтобы увидеть значение, временно напечатаем его через `repr`. Пустой случай '
                            'должен завершить вызов с пустым списком. Для оставшейся ветки временно '
                            'поставим `raise NotImplementedError`. На непустом вводе он остановит пока '
                            'незавершённую ветку, а в приглашении Python появится сообщение об '
                            "остановке. После сохранения отдельно вызовем `repo.search('  ОТЧЁТ 10%  "
                            "')`, `repo.search('отчёт 10%')`, `repo.search('  %_  ')`, `repo.search('')` "
                            "и `repo.search('   ')`. Сравним напечатанные строки, возвращённый пустой "
                            'список и SQL-след.',
                    'requirements': ['Метод `search(self, query)` готовит фрагмент с удалёнными '
                                     'пробелами по краям и в нижнем регистре.',
                                     'В подготовленном значении сохраняются буквальные знаки `%` и `_`.',
                                     'Пустой и пробельный ввод возвращают `[]` до выполнения SQL. На '
                                     'непустом вводе в этой карточке виден промежуточный результат, '
                                     'затем срабатывает временная остановка.'],
                    'result': "Оба варианта дают 'отчёт 10%', у строки со знаками виден '%_'. Пустой и "
                              "пробельный ввод дают '' и [], без SELECT.",
                    'hint': 'Посмотрим, получилась ли пустая строка после обработки. Для исходной строки '
                            'из пробелов и подготовленного значения ответы различаются.',
                    'answer': {'explanation': 'Сравниваем содержимое после подготовки. Наружные пробелы '
                                              'и регистр букв влияют на форму строки, а знаки запроса '
                                              'сохраняются.',
                               'steps': ['Для «  ОТЧЁТ 10%  » после первого действия остаётся «ОТЧЁТ '
                                         '10%». Второе действие даёт «отчёт 10%». Эта форма совпадает с '
                                         'уже набранным нижним регистром. Временно напечатайте repr '
                                         'вычисленного значения, чтобы видеть его границы.',
                                         'Из трёх пробелов получается пустая строка. Возврат [] на этой '
                                         'ветке происходит до работы с Session. Поместите временную '
                                         'остановку дальше, после пустой ветки.',
                                         'Если проверить переданный текст до обработки, три пробела '
                                         'выглядят как непустой текст. Вместо [] сработает временная '
                                         'остановка. По выводу repr видно, почему проверку надо связать '
                                         'с подготовленным значением.',
                                         'Проверьте оба входа с отчётом, затем отдельно строку со '
                                         'знаками и два пустых случая. Сохраните вычисление и ранний '
                                         'выход. Печать и временная остановка нужны лишь для текущей '
                                         'карточки.'],
                               'code': 'fragment = query.strip().lower()\n'
                                       'print(repr(fragment))\n'
                                       'if not fragment:\n'
                                       '    return []\n'
                                       'raise NotImplementedError',
                               'checks': ["На двух вариантах отчёта видим `'отчёт 10%'` перед временной "
                                          "остановкой. На `'  %_  '` видим `'%_'`.",
                                          "На пустом вводе и пробелах видим `''`, затем `[]`. После "
                                          'вызова нет строки SELECT.']}},
                   {'title': 'SQLite находит буквальный фрагмент',
                    'task': 'Продолжим `search` в `app/DBase/repositories.py`. У нас есть два похожих '
                            'названия из учебной базы, 35 «Отчёт 10% ноябрь» и 42 «Отчёт 10X». Прежде '
                            'чем менять метод, предположим, какое название совпадёт с запиской «  ОТЧЁТ '
                            '10%  ». Из раздела «Буквальный фрагмент в запросе» возьмём сравнение '
                            'позиции с нулём и передачу фрагмента отдельным параметром. В разделе '
                            '«Порядок строк и предел выдачи» вспомним, как задать возрастание по id.\n'
                            '\n'
                            'Вместо временной остановки напишем запрос к `title_normalized`. Сохраним '
                            'ранний возврат и уберём проверочную печать. Возьмём импорт функции '
                            'SQLAlchemy из примера. Результат запроса переведём в знакомые Task. После '
                            "сохранения вызовем `print([task.id for task in repo.search('  ОТЧЁТ 10%  "
                            '\')])`. Повторим с `%`, `_`, `"\'А\'"`, `ПЛАН`, `нет` и с пробелами. Найдём '
                            'в следе функцию, колонку и переданное значение. В завершение ещё раз '
                            'вызовем `list()` для проверки полного списка и `select(is_done=False, '
                            'sort_desc=True, limit=2)` для проверки прежней выборки. HTTP продолжает '
                            'задавать предел отдельно.',
                    'requirements': ['Метод `search` использует прежнюю обработку строки и для пустого '
                                     'фрагмента возвращает `[]` без SELECT.',
                                     'Для непустого ввода SQLite выполняет один SELECT. `func.instr` '
                                     'ищет переданный параметром фрагмент в `title_normalized`, '
                                     'совпадение определяется положительной позицией.',
                                     'Результат представлен как `list[Task]` в порядке возрастания id, '
                                     'включая все совпадения. При непустом промахе результат `[]` '
                                     'получен после SELECT.',
                                     'Прежний `list()` возвращает все задачи. Настроенный `select()` '
                                     'сохраняет фильтр, порядок и SQL-предел.'],
                    'result': 'Отчёт со знаком процента и одиночный % дают [35], одиночный _ даёт [27], '
                              'кавычки вокруг А дают [12]. «ПЛАН» даёт [12, 27, 53]. «Нет» даёт [] после '
                              'SELECT, пробелы дают [] без SELECT.',
                    'hint': 'Если пришла 42, посмотрим, какой фрагмент получил SQLite и как условие '
                            'обрабатывает процент. При совпадении посмотрим на позицию начала фрагмента.',
                    'answer': {'explanation': 'SQLite ищет написанный фрагмент в сохранённом названии в '
                                              'нижнем регистре. Положительная позиция показывает '
                                              'совпадение. Отдельная ранняя ветка охватывает пустую '
                                              'записку.',
                               'steps': ['Сохраните ветку из предыдущей карточки. Записка «  ОТЧЁТ 10%  '
                                         '» передаёт в поиск «отчёт 10%». Для пустой записки до '
                                         'обращения к Session возвращается [].',
                                         'Создайте выражение SQLite из двух аргументов: колонки '
                                         'title_normalized и уже подготовленного значения. В следе '
                                         'фрагмент передаётся отдельно. Для строки 35 фрагмент '
                                         'начинается на позиции 1. Для 42 функция возвращает 0, потому '
                                         'что после 10 идёт X.',
                                         'Добавьте к запросу ORM-строк условие положительной позиции и '
                                         'порядок возрастания id. Выполните запрос один раз, '
                                         'преобразуйте полученные строки в привычные Task.',
                                         'Уберите временные печать и остановку. Если ошибочно принять '
                                         'процент за шаблон, название 42 тоже сможет совпасть. Если ищем '
                                         'непустое «нет», SQLite выполнит запрос и ни одна строка не '
                                         'пройдёт. Для пустой записки запроса вообще нет.',
                                         'Сопоставьте фактические id и параметры SQL, затем проверьте '
                                         'одиночные знаки и кавычки. Полный list и настроенная выборка '
                                         'продолжают читаться на той же базе.'],
                               'code': 'position = func.instr(TaskRow.title_normalized, fragment)\n'
                                       'statement = statement.where(position > '
                                       '0).order_by(TaskRow.id.asc())',
                               'checks': ["Для '  ОТЧЁТ 10%  ' получаем [35] без 42. В SQL-следе виден "
                                          "фильтр по instr и значение 'отчёт 10%' в параметрах.",
                                          'Проверьте [27] для `_`, [12] для `"\'А\'"`, [12, 27, 53] для '
                                          '«ПЛАН», пустые результаты «нет» после SELECT и пробелов без '
                                          'SELECT.',
                                          '`repo.list()` даёт все пять id, а `repo.select(is_done=False, '
                                          'sort_desc=True, limit=2)` даёт [53, 35].']}}],
         'result': 'Параметры статуса, порядка и предела обрабатывает SQLite. Поиск читает буквальный '
                   'фрагмент из подготовленного ввода и возвращает Task.'}
    ],

    71: [
        {
            "title": "Первое добавление задачи в SQLite",
            "task": "В своём Planner мы уже читаем задачи из тестовой SQLite по id. Обычное добавление через API и CLI работает с JSON.",
            "tasks": [
                {
                    "title": "Создаём задачу и перечитываем её по id",
                    "task": (
                        "В теории найдём блок «Подготовка Planner» в разделе «Когда другой читатель увидит запись». "
                        "Возьмём оттуда готовые изменения сервиса и сборки и файл `check_sql_create.py`. "
                        "Дополним существующий класс `SqlTaskRepository` в `app/DBase/repositories.py` одним методом `create`. "
                        "Он примет название, приоритет и необязательные метки и вернёт созданную `Task` с номером от SQLite.\n\n"
                        "Из корня Planner запустим `python check_sql_create.py`. Проверка сама создаст временную базу и покажет снимок старой задачи. "
                        "Сверим её с результатом после записи. Сравним номер из ответа POST с данными из новой Session. "
                        "Для прямого вызова сервиса сверим также две метки. В отчёте посмотрим, что произошло при отмене записи, "
                        "при POST без `priority` и при обычном добавлении через JSON. Файлы и номера этого прогона используются только для сверки."
                    ),
                    "requirements": [
                        "В `app/DBase/repositories.py` у существующего `SqlTaskRepository` есть метод `create(self, title, priority, tags=None)`. Прежние `list` и `get` доступны.",
                        "У новой строки используются проверенные сервисом `title` и `priority`, `is_done=False`, свой список меток и `title_normalized` от `title.lower()`. При отсутствии меток список пуст.",
                        "Ключ назначает SQLite. Репозиторий использует переданную Session и возвращает обычную `Task` с id, полученным при `flush`. Подтверждением и отменой управляет готовая область записи сервиса, а Session закрывает её владелец.",
                        "POST с названием `  Купить ЧАЙ  ` и приоритетом `3` возвращает 201 и только `id`, `title`, `priority`, `is_done`. Новая Session находит `Купить ЧАЙ` с тем же положительным id, пустыми метками и `title_normalized='купить чай'`. Старая запись с id `40` и таким же названием сохраняет все прежние поля.",
                        "Вызов `add_task` через готовый `sql_service_context` сохраняет `Оплатить СВЕТ`, приоритет `5`, `False` и метки `['дом', 'счёт']`. Чтение в новой Session подтверждает эти значения, id и `title_normalized='оплатить свет'`.",
                        "После контролируемой отмены новая Session видит прежние строки. POST без `priority` даёт 422. Без SQL-подмены обычное добавление пишет в тестовый JSON, а CLI находит задачу там по id.",
                    ],
                    "result": (
                        "`python check_sql_create.py` завершает все проверки. Номера из POST и прямого вызова совпадают с номерами "
                        "при независимом чтении. Сохраняем метод в своём Planner и фиксируем успешную сверку."
                    ),
                    "hint": (
                        "Вернёмся к карточке «Чехол». В какой момент её номер впервые появился в объекте и в какой момент "
                        "его увидела новая Session?"
                    ),
                    "answer": {
                        "explanation": (
                            "У сервиса уже есть проверенные данные задачи. SQLite выдаёт ключ при вставке. "
                            "Репозиторий преобразует строку в `Task` с этим ключом. Выход из готовой области записи подтверждает "
                            "изменение до успешного ответа, а новое чтение показывает, что осталось в базе."
                        ),
                        "steps": [
                            "В `app/DBase/repositories.py` добавим `create` в существующий класс. Для строки возьмём проверенное название и приоритет, начальный статус `False`, новый список меток и строчную копию названия.",
                            "Оставим ключ SQLite. Передадим объект в уже открытую Session и выполним `flush`, чтобы SQLite вставила строку и вернула её id.",
                            "Преобразуем ORM-строку знакомым `task_from_row`. Сервис выйдет из области записи, а владелец закроет Session после использования.",
                            "Запустим проверку из корня проекта. Сопоставим ответ и данные новой Session. Отдельно сравним старую строку, результат отмены и вывод штатного JSON/CLI.",
                        ],
                        "code": (
                            "    def create(self, title, priority, tags=None) -> Task:\n"
                            "        row = TaskRow(\n"
                            "            title=title,\n"
                            "            priority=priority,\n"
                            "            is_done=False,\n"
                            "            tags=[] if tags is None else list(tags),\n"
                            "            title_normalized=title.lower(),\n"
                            "        )\n"
                            "        self.session.add(row)\n"
                            "        self.session.flush()\n"
                            "        return task_from_row(row)"
                        ),
                        "checks": [
                            "`python check_sql_create.py` показывает 201, совпадение id и новой строки, сохранённые метки и неизменную строку `40`.",
                            "Снимки перед отменой и после неё совпадают. Запрос без приоритета даёт 422. В штатном режиме CLI находит задачу из тестового JSON.",
                            "В `create` нет своего вычисления номера и собственной фиксации транзакции.",
                        ],
                    },
                },
            ],
            "result": "В Planner готов SQL `create`. Проверены назначение id, запись с метками, отдельное чтение и прежний JSON-путь.",
        },
    ],
    "73.1": [
        {
            "title": "Переносим буквальный поиск в SQLite",
            "task": "Команда поиска Planner находит задачи по части названия. Теперь тот же фрагмент должен находить задачи в SQLite. Для этого добавьте один метод к существующему репозиторию и запустите готовую локальную проверку.",
            "tasks": [
                {
                    "title": "Добавить search() в существующий SQL-репозиторий",
                    "task": "В своём Planner откройте `app/DBase/repositories.py`. Сами напишите метод `search(self, query)` в `SqlTaskRepository`: от обработки аргумента до возврата списка задач. Прежняя выборка показывает, где построить SELECT и как получить готовые `Task`. Схема уже хранит `title_normalized`.\n\nДо работы сохраните весь блок «Готовая проверка поиска» в `verify_sql_literal_search.py` рядом с каталогом `app`. Если это имя занято, выберите свободное и подставьте его в команду. Из той же папки запустите `python verify_sql_literal_search.py` своим интерпретатором Planner. Файл создаст временную SQLite с учебными данными. До реализации он напечатает «Данные готовы» и сообщит, что нужно добавить `search`.\n\nПосле правки запустите ту же команду. Сверьте id, SQL и значения параметров. Затем посмотрите вывод знакомого обработчика CLI и результат после двух готовых переименований. Проверочный файл использует одноразовую базу и существующие операции записи.\n\nГотовая проверка поиска\n\n```python\nimport json\nimport re\nfrom pathlib import Path\nfrom tempfile import TemporaryDirectory\n\nfrom sqlalchemy import event, select\nfrom sqlalchemy.engine import URL\n\nfrom app.DBase.import_tasks import import_snapshot\nfrom app.DBase.models import Base, TaskRow\nfrom app.DBase.repositories import SqlTaskRepository\nfrom app.DBase.session import create_session_factory\nfrom app.cli import handle_search\nfrom app.core.engine import make_engine\nfrom app.models import Task\nfrom app.service import PlannerService\n\n\nTITLES = [\n    (15, \"Сверка 500\"), (11, \"Сверка 'май'\"), (4, \"СВЕРКА 50%\"),\n    (19, \"TEAM Sync\"), (7, \"Сверка_май\"), (6, \"Сверка 500\"),\n]\nCASES = [\n    (\"свеРКА\", [4, 6, 7, 11, 15], \"сверка\"),\n    (\"  СВЕРКа 500  \", [6, 15], \"сверка 500\"),\n    (\"  TEAM SYNC  \", [19], \"team sync\"),\n    (\"%\", [4], \"%\"),\n    (\"_\", [7], \"_\"),\n    (\"'\", [11], \"'\"),\n    (\"черновик\", [], \"черновик\"),\n    (\"\", [], None),\n    (\"   \", [], None),\n]\n\n\ndef service_for(session):\n    return PlannerService(\n        repository=SqlTaskRepository(session),\n        write_scope=session.begin,\n    )\n\n\ndef check_matches(engine, sessions):\n    print(\"\\nСовпадения, SQL и параметры\")\n    for query, expected_ids, expected_parameter in CASES:\n        statements = []\n\n        def record(_connection, _cursor, sql, values, _context, _many):\n            statements.append((sql, values))\n\n        with sessions() as session:\n            event.listen(engine, \"before_cursor_execute\", record)\n            try:\n                tasks = SqlTaskRepository(session).search(query)\n            finally:\n                event.remove(engine, \"before_cursor_execute\", record)\n\n        assert isinstance(tasks, list), \"Результат должен быть списком.\"\n        assert all(isinstance(task, Task) for task in tasks), \"Нужны объекты Task.\"\n        ids = [task.id for task in tasks]\n        print(repr(query), \"→\", ids, f\"| запросов: {len(statements)}\")\n        assert ids == expected_ids, f\"Для {query!r} ожидались id {expected_ids}.\"\n        if query == \"%\":\n            assert tasks[0].title == \"СВЕРКА 50%\" and tasks[0].tags == [\"work\"]\n\n        if expected_parameter is None:\n            assert not statements, \"Пустой фрагмент не должен обращаться к базе.\"\n            continue\n\n        assert len(statements) == 1, \"Нужен один запрос поиска.\"\n        sql, values = statements[0]\n        print(\"SQL:\", sql)\n        print(\"Параметры:\", values)\n        compact_sql = \" \".join(sql.lower().split())\n        assert compact_sql.startswith(\"select \"), \"Ожидался SELECT.\"\n        _, where, predicate = compact_sql.partition(\" where \")\n        assert where and \"title_normalized\" in predicate, (\n            \"Нужно условие поиска по готовой поисковой копии.\"\n        )\n        _, order, ordering = compact_sql.partition(\" order by \")\n        assert order and re.search(r\"\\bid\\b\", ordering), \"Задайте порядок по id в SQL.\"\n        bound_values = values.values() if isinstance(values, dict) else values\n        assert expected_parameter in bound_values, (\n            \"Обработанный фрагмент должен быть отдельным параметром.\"\n        )\n\n\ndef check_cli(sessions):\n    print(\"\\nCLI\")\n    for query, expected in [\n        (\"%\", [\"[4] СВЕРКА 50% | приоритет: 2 | не завершена\"]),\n        (\"черновик\", [\"Совпадений не найдено.\"]),\n        (\"   \", [\"Введите фрагмент названия.\"]),\n    ]:\n        output = []\n        with sessions() as session:\n            handle_search(service_for(session), read=lambda prompt: query, write=output.append)\n        print(repr(query), \"→\", output)\n        assert output == expected, f\"Проверьте вывод CLI для {query!r}.\"\n\n\ndef find(sessions, query):\n    with sessions() as session:\n        return service_for(session).search_tasks(query)\n\n\ndef check_rename(sessions):\n    print(\"\\nПереименование\")\n    previous_fragment = \"сверка_май\"\n    original = find(sessions, previous_fragment)\n    assert [task.id for task in original] == [7]\n    original_task = original[0]\n    assert original_task.title == \"Сверка_май\" and original_task.tags == [\"work\"]\n\n    for operation, new_title, new_fragment in [\n        (\"PUT\", \"Расчёт_июнь\", \"расчёт_июнь\"),\n        (\"PATCH\", \"Отчёт_июль\", \"отчёт_июль\"),\n    ]:\n        with sessions() as session:\n            service = service_for(session)\n            if operation == \"PUT\":\n                service.replace_task(\n                    7, title=new_title, priority=original_task.priority,\n                    is_done=original_task.is_done,\n                )\n            else:\n                service.patch_task(7, title=new_title)\n\n        old_ids = [task.id for task in find(sessions, previous_fragment)]\n        new_tasks = find(sessions, new_fragment)\n        new_ids = [task.id for task in new_tasks]\n        print(operation, repr(previous_fragment), \"→\", old_ids, \"|\", repr(new_fragment), \"→\", new_ids)\n        assert old_ids == [] and new_ids == [7], \"Сверьте поисковую копию после записи.\"\n        assert new_tasks[0].title == new_title and new_tasks[0].tags == original_task.tags\n        previous_fragment = new_fragment\n\n\ndef main():\n    with TemporaryDirectory() as temporary:\n        directory = Path(temporary)\n        snapshot = directory / \"fixture.json\"\n        database = directory / \"fixture.sqlite3\"\n        snapshot.write_text(json.dumps([\n            {\n                \"id\": number, \"title\": title, \"priority\": 2, \"is_done\": False,\n                \"tags\": [\"work\"] if number in (4, 7) else [],\n            }\n            for number, title in TITLES\n        ], ensure_ascii=False), encoding=\"utf-8\")\n\n        url = URL.create(\"sqlite\", database=database.as_posix())\n        engine = make_engine(url.render_as_string(hide_password=False))\n        try:\n            Base.metadata.create_all(engine)\n            assert import_snapshot(snapshot, database) == len(TITLES)\n            sessions = create_session_factory(engine)\n            with sessions() as session:\n                rows = session.scalars(select(TaskRow).order_by(TaskRow.id)).all()\n                ids = [row.id for row in rows]\n                assert ids == [4, 6, 7, 11, 15, 19]\n                assert rows[0].title_normalized == \"сверка 50%\"\n                print(\"Данные готовы:\", ids, \"| поисковая копия id 4:\", rows[0].title_normalized)\n\n            if not hasattr(SqlTaskRepository, \"search\"):\n                raise SystemExit(\"Теперь добавьте метод search в SqlTaskRepository.\")\n\n            check_matches(engine, sessions)\n            check_cli(sessions)\n            check_rename(sessions)\n            print(\"\\nВсе проверки поиска прошли.\")\n        finally:\n            engine.dispose()\n\n\nif __name__ == \"__main__\":\n    main()\n```",
                    "requirements": [
                        "Пустой или пробельный `query` даёт `[]` при нуле запросов. В непустом случае используется `query.strip().lower()`.",
                        "Один SELECT находит буквальный фрагмент в существующем `title_normalized`. Обработанный фрагмент передаётся отдельно в параметрах; SELECT отбирает строки в SQLite и упорядочивает их по возрастанию id.",
                        "Результат состоит из `Task` с исходным `title` и `tags`. «свеРКА» даёт `[4, 6, 7, 11, 15]`, «  СВЕРКа 500  » даёт `[6, 15]`, «  TEAM SYNC  » даёт `[19]`. Процент, подчёркивание и кавычка дают `[4]`, `[7]` и `[11]`. «черновик» даёт `[]` через один SELECT.",
                        "Через подготовленный SQL-сервис CLI для «%» выводит только задачу 4 в привычном формате. Для «черновик» и пробелов выводит «Совпадений не найдено.» и «Введите фрагмент названия.» соответственно.",
                        "После готового PUT для id 7 «сверка_май» даёт `[]`, а «расчёт_июнь» находит 7. После готового PATCH «расчёт_июнь» даёт `[]`, а «отчёт_июль» находит 7. Исходные теги задачи сохраняются. Штатный запуск продолжает использовать JSON."
                    ],
                    "result": "Проверка выводит блоки «Совпадения, SQL и параметры», «CLI» и «Переименование» и заканчивается сообщением «Все проверки поиска прошли.». В репозитории добавлен один предметный метод.",
                    "hint": "Если процент нашёл лишнюю строку, вернитесь к сравнению режимов в опыте с витриной. Для кавычки сравните текст запроса и параметры в выводе опыта. Для результата после записи сопоставьте исходный заголовок и поисковую копию.",
                    "answer": {
                        "explanation": "Краевые пробелы убираем у пользовательского ввода. Прежний Python lower() приводит его к тому же виду, в котором записана поисковая копия. Положительная позиция от instr даёт условие SQL; строку SQLAlchemy передаёт драйверу как отдельное значение. Пустое значение возвращает [] до выполнения запроса.",
                        "steps": [
                            "В файле репозитория проверьте, что импортированы func и select. Добавьте метод к существующему классу.",
                            "Нормализуйте запрос прежним способом. Если фрагмент пуст, верните пустой список сразу.",
                            "Отберите строки с положительной позицией. Явно задайте сортировку по id. Получите только выбранные строки и преобразуйте их через task_from_row.",
                            "Сравните вывод для «%» и одинарной кавычки, затем проверьте, что после переименования поиск видит новую запись по тому же id с прежними тегами."
                        ],
                        "code": "from sqlalchemy import func, select\n\n# Метод класса SqlTaskRepository\ndef search(self, query: str) -> list[Task]:\n    fragment = query.strip().lower()\n    if not fragment:\n        return []\n\n    statement = (\n        select(TaskRow)\n        .where(func.instr(TaskRow.title_normalized, fragment) > 0)\n        .order_by(TaskRow.id)\n    )\n    rows = self.session.scalars(statement).all()\n    return [task_from_row(row) for row in rows]",
                        "checks": [
                            "Пустые строки возвращают [] при нуле запросов.",
                            "В отчёте непустого поиска видны WHERE, ORDER BY и связанное значение. Процент находит только id 4, кавычка только id 11.",
                            "После каждой записи старый фрагмент перестаёт находить id 7, новый находит его с прежними тегами."
                        ]
                    }
                }
            ],
            "result": "Буквальный поиск работает в SQL-репозитории. Локальная проверка подтверждает совпадения, вывод CLI и чтение после переименования."
        }
    ],

    74: [{'title': 'Открываем Session на одну команду CLI',
      'task': 'В вашем Planner уже есть диспетчер CLI и SQL-контекст. Откройте «Что мы будем делать в '
              'практике». В разделе «Проверочная консоль Planner» подготовьте одну задачу и запомните её id. '
              'Эту консоль используйте во всех трёх карточках.',
      'tasks': [{'title': 'Очертим ресурс команды',
                 'task': 'Откройте «Когда команде нужен ресурс». Из примера с реестром перенесите два приёма: '
                         'передачу фабрики без вызова и ограничение работы с журналом блоком `with`.\n'
                         '\n'
                         'В `app/cli.py` добавьте к `run` именованный аргумент `context_factory`. Пробный '
                         'запуск передаёт фабрику и `None` вместо готового сервиса. Проследите путь от `run` до '
                         'найденного обработчика и сами выберите точку входа и выхода контекста. Сохраните '
                         'обычный вызов `run(service)` и прежние обработчики.\n'
                         '\n'
                         'Обновите `run` в проверочной консоли, как показано в теории. Запустите `run(None, '
                         'context_factory=open_tasks)`. Введите `find`, сохранённый id и `exit`.',
                 'requirements': ['`run` принимает именованный `context_factory`. Вызовы с готовым сервисом '
                                  'продолжают работать.',
                                  'Распознавание команды, её сообщения и вызов существующего обработчика '
                                  'сохраняют прежний договор. В SQL-прогоне сервис для вызова получен из '
                                  'короткого контекста.',
                                  'Поиск показывает задачу с сохранённым id. Запрос номера и результат поиска '
                                  'находятся внутри одной пары событий с общей меткой. Следующее приглашение '
                                  'появляется после закрытия.'],
                 'result': 'В выводе видна «Запланировать звонок» с тем же id, который напечатала консоль при '
                           'подготовке. Вы можете показать точку получения сервиса в своём коде и сопоставить '
                           'её с парой событий.',
                 'hint': 'Найдите место, в котором уже известно, что ввод соответствует обработчику. Тогда '
                         'можно решить, когда нужен ресурс.',
                 'answer': {'explanation': 'Передача фабрики откладывает создание контекста до выбора '
                                           'обработчика. Выбранный обработчик получает сервис для своей '
                                           'команды. Прежняя сборка передаёт готовый сервис напрямую.',
                            'steps': ['Проследите find: run читает строку, execute_command приводит её к имени '
                                      'команды и находит обработчик. Отметьте место вызова этого обработчика.',
                                      'Добавьте необязательный именованный параметр context_factory у run и '
                                      'передайте его диспетчеру. В диспетчере сохраняйте ранние завершения и '
                                      'обычный путь с готовым сервисом.',
                                      'При вызове выбранного обработчика с фабрикой войдите в контекст и '
                                      'передайте полученный сервис в существующий вызов. Выйдите из with до '
                                      'возврата признака продолжения.',
                                      'На find по сохранённому id появляется, например, open(S1). Между этим '
                                      'событием и close(S1) видны запрос номера и строка задачи. Следующее '
                                      'приглашение появляется после close. Если open стоит до первого '
                                      'приглашения, ресурс открыт раньше распознавания команды.'],
                            'code': '# В run передаём тот же объект функции в execute_command:\n'
                                    'keep_running = execute_command(service, command, read, write, '
                                    'context_factory=context_factory)\n'
                                    '\n'
                                    '# В execute_command после прежних ранних возвратов:\n'
                                    'if context_factory is None:\n'
                                    '    handler(service, read=read, write=write)\n'
                                    'else:\n'
                                    '    with context_factory() as command_service:\n'
                                    '        handler(command_service, read=read, write=write)\n'
                                    'return True',
                            'checks': ['В консоли вызовите run(None, context_factory=open_tasks), введите find и '
                                      'свой id. Закрытие с той же меткой происходит до следующего приглашения. '
                                      'Введите exit и посмотрите, добавилось ли новое открытие.']}},
                {'title': 'Предскажем границы на смешанном вводе',
                 'task': 'Вернитесь к «Когда команде нужен ресурс». Перенесите из примера с реестром различие '
                         'между необходимостью открыть ресурс и результатом чтения.\n'
                         '\n'
                         'До запуска запишите прогноз для четырёх вводов по порядку: ` FiNd ` с вашим id, '
                         '`fiind`, `find` с id 404 и `exit`. Укажите, когда для каждого ввода вы ждёте '
                         'открытия, закрытия и нового приглашения. Снова запустите `run(None, '
                         'context_factory=open_tasks)` в той же консоли и введите эти команды. Если прогноз и '
                         'журнал различаются, найдите первое расхождение и проверьте соответствующую границу в '
                         'CLI.',
                 'requirements': ['У обработчиков с данными отдельные пары открытия и закрытия. Ввод без '
                                  'обращения к данным сохраняет обычное поведение и не добавляет событий '
                                  'Session.',
                                  'На каждом новом приглашении активных меток нет. В черновике записаны прогноз '
                                  'для четырёх вводов и его сверка с журналом.'],
                 'result': 'Вы сопоставили каждый ввод с фактическим журналом и объяснили различие между '
                           'опечаткой и поиском без результата.',
                 'hint': 'Вспомните, в какой момент поиск узнаёт, существует ли задача с номером 404.',
                 'answer': {'explanation': 'Открытие зависит от распознавания команды. Результат поиска '
                                           'становится известен позже, при обращении к данным.',
                            'steps': ['После нормализации « FiNd » превращается в find. Ждём открытия, запроса '
                                      'номера, строки найденной задачи и закрытия до нового приглашения. '
                                      'Запишите фактическую метку, например S2.',
                                      'fiind не распознаётся. Диспетчер печатает «Неизвестная команда.» и '
                                      'возвращается к приглашению с пустым active.',
                                      'find 404 распознан. В новой Session, например S3, начинается поиск. '
                                      'Появляется «Задача не найдена.», затем закрывается S3. Отсутствие задачи '
                                      'выяснилось только после чтения.',
                                      'exit завершает цикл. Если в журнале появилась ещё одна пара или та же '
                                      'метка держится через несколько приглашений, найдите первый ввод с лишним '
                                      'событием и проверьте место входа или выхода из контекста.'],
                            'code': 'run(None, context_factory=open_tasks)\n'
                                    '# Ввод: FiNd с пробелами, сохранённый id, fiind, find, 404, exit',
                            'checks': ['Две пары относятся к разным меткам. У опечатки и выхода событий Session '
                                      'нет. Каждое новое приглашение появляется после закрытия предыдущей '
                                      'Session.']}},
                {'title': 'Докажем закрытие на исключении',
                 'task': 'Откройте «Как доказать закрытие при исключении». В примере с отчётом проследите одну '
                         'метку от начала работы до внешнего перехвата.\n'
                         '\n'
                         'В той же консоли выполните фрагмент «Сбой на запросе номера» из практического раздела '
                         'теории. Найдите метку Session начатого `find`, выпишите относящиеся к ней строки и '
                         'состояние на выходе. Объясните, какое наблюдение подтверждает закрытие и что можно '
                         'было бы заключить по одной строке ошибки.',
                 'requirements': ['Помеченный сбой дошёл до внешней проверки. Поиск не вывел задачу.',
                                  'В выводе ученика указаны метка начатой Session, события, достаточные для '
                                  'вывода о её закрытии, и фактическое состояние на границе выхода.'],
                 'result': 'Вы обосновали вывод об освобождении ресурса по журналу одной прерванной команды.',
                 'hint': 'Найдите событие с той же меткой, что у начавшегося обработчика, и проверьте, когда '
                         'оно произошло.',
                 'answer': {'explanation': 'Исключение сообщает о прерывании. Закрытие подтверждается действием '
                                           'Session.close(), отмеченным в журнале после завершения этого '
                                           'метода.',
                            'steps': ['Найдите строку handler с фактической меткой, например S4. Find уже начал '
                                      'работу и запросил номер. Fault(S4) появляется вместо ответа на этот '
                                      'запрос, поэтому результата поиска нет.',
                                      'Прочтите события после сбоя. При выходе из with срабатывает close(S4). '
                                      'Затем внешний перехват даёт caught(S4), а active=[] подтверждает пустое '
                                      'состояние.',
                                      'Если в журнале только fault(S4) и caught(S4), известна судьба '
                                      'исключения. Если виден close(S3), закрылась другая Session. Для S4 нужно '
                                      'её собственное закрытие.'],
                            'code': 'Выполните «Сбой на запросе номера» в той же консоли после двух обычных '
                                    'прогонов.',
                            'checks': ['С одной и той же фактической меткой следуют open, handler, fault, close, '
                                      'caught. После перехвата видно active=[].']}}],
      'result': 'CLI использует отдельную Session для каждой команды с данными. Обычный путь, ввод без ресурса '
                'и освобождение при сбое подтверждаются разными наблюдениями.'}],
    "75.1": [
        {
            "title": "Один источник в рабочей сборке Planner",
            "task": (
                "Работаем со своим Planner после общего перехода. Результат сверки, общий адрес SQLite "
                "и чтения API и CLI сохранены в result.md в своей папке запуска внутри project/data/cutover/. "
                "Откроем этот отчёт и подготовим проверку в том же окружении, в котором запускается Planner."
            ),
            "tasks": [
                {
                    "title": "Разорвать старую зависимость и проверить сборку",
                    "task": (
                        "В теории откроем раздел «Готовая проверка для Planner» и целиком скопируем "
                        "check_sql_only.py в корень своего project/, рядом с app/ и tests/. Если имя занято, "
                        "сохраним файл как check_sql_only_local.py и подставим это имя в команды baseline, cleanup и check. "
                        "Сначала из project/ запустим python check_sql_only.py baseline. Сверим путь вызовов и новый id "
                        "на временной SQLite. При неудачном входе вернёмся к отчёту о переключении. "
                        "На карте проследим реальные вызовы от API и CLI к сервису, начав с build_service в app/main.py "
                        "и PlannerService в app/service.py. Если API подключён через make_http_planner, проследим и этот путь. "
                        "Сами уберём связь сборки с файловым объектом. Сохраним штатную передачу SQL-репозитория и области записи. "
                        "Затем запустим python check_sql_only.py cleanup для механической уборки. "
                        "Прочитаем список правок и запустим python check_sql_only.py check. "
                        "Сравним обычную работу без старого файла, отказ с контрольным JSON и чистую базу до и после явного импорта. "
                        "Запустим из project/ полный набор командой python -m pytest -q. "
                        "В README перенесём готовый фрагмент о запуске на SQLite из того же раздела теории."
                    ),
                    "requirements": [
                        "Входной отчёт показывает один временный URL, новую задачу и её чтение в обеих обычных точках после нового запуска.",
                        "Общая сборка сервиса использует переданный репозиторий. В штатных API и CLI остаётся SQL-цепочка с короткой Session и областью записи. В карте сборки отсутствуют старый файловый адаптер, JSON_PATH и запасной путь к JSON или памяти.",
                        "cleanup переносит зависимость API, публичную карточку без тегов и настройки разных клиентов на временную SQLite. Исходные варианты тестов сохраняются вне автосбора. Полный набор тестов проекта проходит после cleanup и check.",
                        "На доступной SQLite при отсутствующем старом файле API создаёт «Сверить этикетки» с приоритетом 3. CLI в новом процессе читает и завершает её по выданному id. Новый API видит is_done=True, JSON не появляется.",
                        "При временном адресе внутри отсутствующей папки чтение и попытка записи останавливаются в обоих интерфейсах. Контрольная «Архивная метка» не выдаётся. Нет ложного успеха, открытий контрольного файла и изменений его байтов.",
                        "В чистой временной базе после init и обычного запуска таблица пуста. Только явный вызов импортёра переносит контрольную строку со всеми полями. Повтор для непустой цели сохраняет данные без повторного переноса.",
                        "README задаёт общий DATABASE_URL, явный init и команды запуска API и CLI из одной папки. Инструкции с JSON описывают отдельный импорт."
                    ],
                    "result": (
                        "В отчёте check все группы проходят. По карте и наблюдениям можно показать, что штатные API и CLI "
                        "работают через SQLite, а архив читает только явно запущенный импортёр. Полный pytest прошёл. "
                        "В README есть рабочая последовательность команд для обоих интерфейсов."
                    ),
                    "hint": (
                        "В карте из раздела «Кто выбирает хранилище» проследим, какой объект получает сервис "
                        "из каждой обычной точки входа. В отчёте «Отказ» сравним три независимых сигнала: "
                        "итог операции, число открытий контрольного файла и состояние его байтов."
                    ),
                    "answer": {
                        "explanation": (
                            "Два обычных входа передают репозиторий, созданный из ресурса текущего запроса или команды. "
                            "Общая сборка принимает этот объект и область записи. Когда путь к файловому адаптеру убран, "
                            "ошибка SQLite доходит до вызывающей стороны. Отдельный импортёр по-прежнему получает выбранный "
                            "путь при явном запуске."
                        ),
                        "steps": [
                            "По baseline находим активные вызовы от API и CLI и прежний переходный вход. Убеждаемся, что обе обычные цепочки уже передают SQL-репозиторий.",
                            "Делаем repository обязательным входом фабрики и PlannerService. Убираем выбор по storage/settings и оборачивание файлового объекта. Согласуем существующие вызовы repository= и оставляем передачу write_scope.",
                            "Запускаем cleanup. Оснастка убирает осиротевшие импорты и настройки. Проверки Session, публичных полей, cookies и CLI остаются в наборе на временной SQLite. Исходные варианты сохранены в резерве.",
                            "Запускаем check. Сначала связываем id между процессами. Затем сверяем отказ и нулевое число открытий архива. Наконец, сравниваем пустую таблицу до явной команды с записью после неё.",
                            "Запускаем python -m pytest -q из project/. Итог готов, когда весь набор завершился без ошибок. В README указываем общий DATABASE_URL для обоих терминалов, затем явный init и команды запуска из раздела теории."
                        ],
                        "code": (
                            "# app/main.py\n"
                            "def build_service(repository, *, write_scope=None):\n"
                            "    return PlannerService(repository=repository, write_scope=write_scope)\n\n"
                            "# В существующем классе app/service.py\n"
                            "def __init__(self, repository, *, write_scope=None):\n"
                            "    self.repository = repository\n"
                            "    self.write_scope = nullcontext if write_scope is None else write_scope\n"
                        ),
                        "checks": [
                            "В обычных SQL-вызовах продолжают передаваться repository и write_scope. Короткие SQL-ресурсы сохраняются.",
                            "Отсутствующий файл и исправная SQLite дают обычный успех. Существующий контрольный файл и недоступная SQLite дают отказ и ноль обращений.",
                            "Схема существует до импорта. Сам импорт запускается явно и принимает путь к существующей пустой базе.",
                            "Команда python -m pytest -q сообщает об успешном завершении всего набора, а README описывает запуск CLI и API с общей SQLite."
                        ]
                    }
                }
            ],
            "result": (
                "Planner работает на одной SQLite. Рабочие данные и архив сохранены. README описывает запуск, полный набор проверок проходит."
            )
        }
    ],
    '72.2': [
        {
            "title": "Удаляем завершённую задачу",
            "task": "Продолжаем работу в своём Planner. По сохранённым данным завершённой задачи и её соседа восстановим две записи на временной SQLite. Удалим одну из них по id и проверим результат.",
            "result": "Проверки готовности, репозитория и интерфейсов проходят. Удалённая Task доступна после закрытия Session, повторный DELETE получает 404, соседняя строка сохраняется.",
            "tasks": [
                {
                    "title": "Добавить удаление в SQL-репозиторий",
                    "task": "Возьмём из вывода готовой проверки завершения две строки с DONE_TASK и CONTROL_TASK. В конце теории, в блоке «Подготовка к практике», скопируем область записи в `project/app/service.py` и готовую проверку в `project/tests/test_lesson_task_delete.py`. Вставим в проверку обе строки из вывода. Из `project/` в окружении Planner запустим `python -m pytest -v tests/test_lesson_task_delete.py -k ready`.\n\nЗатем откроем `project/app/DBase/repositories.py` и сами добавим метод `delete` в `SqlTaskRepository`. Когда задача найдена, сервису нужна обычная Task со всеми прежними полями и тегами. Когда id отсутствует, сервис должен получить None. Проверим оба случая командой `python -m pytest -v tests/test_lesson_task_delete.py -k repository`.\n\nЗапустим весь модуль командой `python -m pytest -v tests/test_lesson_task_delete.py`. Он проверит DELETE и GET, сообщение CLI, состояние соседней строки и откат при ошибке перед commit. По своему методу объясним, почему повторный DELETE получает 404.",
                    "requirements": [
                        "Метод `SqlTaskRepository.delete(self, task_id: int) -> Task | None` ищет одну строку по переданному id в имеющейся Session.",
                        "При совпадении метод возвращает обычную Task с исходными `id`, `title`, `priority`, `is_done` и `tags`. Преобразование выполнено до `Session.delete` выбранной ORM-строки. Управление транзакцией и сроком жизни Session принадлежит вызывающему коду.",
                        "При отсутствии строки результат равен `None`. Свежая Session видит обе исходные записи без изменений.",
                        "Для id из `DONE_TASK` первый HTTP DELETE даёт 204 с пустым телом после commit. Свежая Session подтверждает отсутствие цели и совпадение всех полей `CONTROL_TASK`. Следующий GET и повторный DELETE того же id дают 404. CLI сообщает «Задача не найдена.».",
                        "При сбое после flush до commit успешный 204 отсутствует. Свежая Session видит обе исходные строки."
                    ],
                    "hint": "Вернёмся к схеме «Как отметить одну строку к удалению» и назовём результат каждой ветви поиска. Если повторный запрос выглядит успешным, сравним `None` из репозитория и пустое тело HTTP 204 в разделе «Когда DELETE сообщает об успехе».",
                    "answer": {
                        "explanation": "Репозиторий выбирает строку по первичному ключу. При совпадении он передаёт сервису обычную Task с сохранёнными полями. Область записи в сервисе подтверждает изменение до HTTP 204. Если строки уже нет, сервис получает None и поднимает предметную ошибку, которую обработчик превращает в 404.",
                        "steps": [
                            "Откроем `project/app/DBase/repositories.py` и добавим метод `delete` в существующий `SqlTaskRepository`. Метод получает id и ищет TaskRow в своей Session.",
                            "Если строки нет, сразу вернём `None`. Сервис сможет сообщить об отсутствии, а таблица сохранит прежнее состояние.",
                            "Если строка найдена, получим обычную Task функцией `task_from_row`. Она переносит все предметные поля, включая отдельный список тегов.",
                            "Передадим найденную строку в `Session.delete` и вернём сохранённую Task. Фиксация произойдёт при выходе из готовой области сервиса.",
                            "Запустим проверку репозитория на двух id и на промахе. Затем запустим весь тестовый файл и сопоставим 204, повторный 404 и результаты независимого чтения, включая случай отката."
                        ],
                        "code": "    def delete(self, task_id: int) -> Task | None:\n        row = self.session.get(TaskRow, task_id)\n        if row is None:\n            return None\n\n        deleted = task_from_row(row)\n        self.session.delete(row)\n        return deleted",
                        "checks": [
                            "Из `project/` команда `python -m pytest -v tests/test_lesson_task_delete.py -k repository` подтверждает результат метода для обоих id и для отсутствующей задачи.",
                            "Полный запуск `python -m pytest -v tests/test_lesson_task_delete.py` подтверждает вход, обе ветви метода, HTTP, CLI и отмену при сбое."
                        ]
                    },
                    "result": "Проверка репозитория проходит для обоих id и для отсутствующей задачи. Полный прогон подтверждает пустой 204, повторный 404, сохранность соседа и откат при сбое."
                }
            ]
        }
    ],
    '73.2': [{'title': 'Сводка задач Planner',
              'task': 'Работаем в существующем Planner. В его учебной ветке SQL-репозиторий уже выбирает задачи по '
                      'статусу, а сервис умеет завершать и удалять их. Для обеих карточек используем три строки: '
                      '24 «Сверить опись» (False), 41 «Сверить опись» (True) и 58 «Снять показания» (True).\n'
                      '\n'
                      'В терминале из папки `project` запустите `python` из активированного окружения Planner. На '
                      'Windows из `project` также можно запустить `.\\.venv\\Scripts\\python.exe`. При приглашении '
                      '`>>>` вставьте блок целиком, от `exec(` до последней скобки. Он создаст отдельный файл '
                      'SQLite во временной папке и напечатает «Учебная база готова». Подготовленные данные '
                      'исчезнут после выхода из Python. Код подготовки в проект сохранять не нужно.\n'
                      '\n'
                      '```python\n'
                      "exec('''\n"
                      'from atexit import register\n'
                      'from pathlib import Path\n'
                      'from tempfile import TemporaryDirectory\n'
                      'from sqlalchemy import URL, create_engine, delete, text\n'
                      'from sqlalchemy.orm import Session\n'
                      'from app.DBase.models import Base, TaskRow\n'
                      'from app.DBase.repositories import SqlTaskRepository\n'
                      '\n'
                      'scratch = TemporaryDirectory(prefix="planner-stats-")\n'
                      'location = Path(scratch.name) / "practice.sqlite3"\n'
                      'engine = create_engine(URL.create("sqlite", database=str(location)))\n'
                      'register(engine.dispose)\n'
                      'Base.metadata.create_all(engine)\n'
                      'rows = [(24, "Сверить опись", False), (41, "Сверить опись", True),\n'
                      '        (58, "Снять показания", True)]\n'
                      '\n'
                      'def reset(data):\n'
                      '    with Session(engine) as session, session.begin():\n'
                      '        session.execute(delete(TaskRow))\n'
                      '        session.add_all(TaskRow(id=number, title=title, priority=2,\n'
                      '                                is_done=state, tags=[], title_normalized=title.lower())\n'
                      '                        for number, title, state in data)\n'
                      '\n'
                      'def probe(number, wanted_status):\n'
                      '    with Session(engine) as session:\n'
                      '        task = session.get(TaskRow, number)\n'
                      '        if task is None:\n'
                      '            print("Такого id в учебной таблице нет")\n'
                      '            return\n'
                      '        statement = text("SELECT CASE WHEN :matches THEN :number ELSE NULL END")\n'
                      '        print(session.execute(statement, {\n'
                      '            "matches": task.is_done == wanted_status, "number": task.id,\n'
                      '        }).scalar_one())\n'
                      '\n'
                      'reset(rows)\n'
                      'print("Учебная база готова")\n'
                      "''')\n"
                      '```',
              'tasks': [{'title': 'Проследим одну строку',
                         'task': 'Вернитесь к разделу «Как одна строка попадает в счётчик». Возьмём из примера '
                                 'переход от условия строки к id или NULL и правило, по которому COUNT учитывает '
                                 'ячейку.\n'
                                 '\n'
                                 'Найдём среди двух задач «Сверить опись» завершённую. На черновике рядом с '
                                 'редактором запишем её id и три короткие строки: что получит CASE открытых, что '
                                 'получит CASE завершённых и почему общий счётчик учитывает эту задачу. Рядом с '
                                 'двумя CASE запишем, прибавят ли они единицу к своему итогу.\n'
                                 '\n'
                                 'После прогноза в том же Python запустим `probe(выбранный_id, False)` для '
                                 'открытых и `probe(выбранный_id, True)` для завершённых, подставив свой id. Проба '
                                 'сверяет статус одной строки с переданным статусом и показывает значение CASE. '
                                 'Сохраним вывод и своё объяснение для следующей карточки.',
                         'requirements': ['Указан id завершённой задачи из двух записей «Сверить опись». Оба '
                                          'вызова пробы используют этот id.',
                                          'До вызовов зафиксированы два значения для CASE и вклад строки в общий, '
                                          'открытый и завершённый счётчики. NULL соответствует `None` в '
                                          'терминале.'],
                         'result': 'Вывод обоих вызовов совпадает с прогнозом для выбранной строки. По заметке '
                                   'видно, в каких двух счётчиках эта строка прибавляет единицу.',
                         'hint': 'В таблице у задач одинаковые заголовки. Посмотрите, какое поле различает их '
                                 'состояния, и проверьте каждую колонку отчёта отдельно.',
                         'answer': {'explanation': 'Заголовок не определяет состояние задачи. По `is_done` найдём '
                                                   'нужную строку, а затем отдельно проследим, что для неё '
                                                   'вычислят два CASE.',
                                    'steps': ['У задач 24 и 41 один заголовок. У 24 статус False, у 41 True. Берём '
                                              'id 41, потому что условие просит завершённую задачу.',
                                              'Проверяем 41 как открытую. Сравнение с False не подходит, поэтому '
                                              'CASE для открытых даёт NULL. COUNT оставляет счёт открытых без '
                                              'прибавления.',
                                              'Проверяем 41 как завершённую. Сравнение с True подходит, CASE '
                                              'возвращает id 41. У колонки с завершёнными появляется одно '
                                              'заполненное значение. В общей колонке id 41 тоже заполнен. Вклад '
                                              'получается all=1, open=0, done=1.',
                                              'Сравниваем с пробой. Первый вызов показывает None, второй 41. Если '
                                              'случайно выбрать 24, первым выйдет 24, а вторым None. Такое '
                                              'расхождение сначала проверяем по id и исходному статусу.'],
                                    'code': '# В Python с уже готовой учебной базой\n'
                                            'probe(41, False)\n'
                                            'probe(41, True)',
                                    'checks': ['Для 41 вывод по порядку: `None`, `41`. Для второй одноимённой '
                                               'задачи порядок вклада меняется: `24`, `None`.',
                                               'В своей заметке оставьте id 41 и пояснение, почему общий и '
                                               'завершённый счётчики учитывают его.']}},
                        {'title': 'Сверим именованные числа до HTTP',
                         'task': 'Возьмём из раздела «Как одна строка попадает в счётчик» правило вклада id и '
                                 'NULL. Из раздела «Как прочитать итоги по именам» возьмём метки колонок и чтение '
                                 'ячеек по ключу.\n'
                                 '\n'
                                 'В `project/app/DBase/repositories.py` добавим `statistics()` в существующий '
                                 '`SqlTaskRepository` или заменим тело заготовки этого метода. Напишем один '
                                 'запрос, который возвращает общий счёт задач и два счёта по статусам. '
                                 'Воспользуемся своей заметкой: под каким именем должна прибавить единицу '
                                 'выбранная задача? Нужна одна строка результата с тремя числами, из которых '
                                 'соберём обычный словарь.\n'
                                 '\n'
                                 'После сохранения закроем старый Python через `exit()`, запустим новый из '
                                 '`project` и снова вставим блок подготовки. Свежий процесс увидит правку и '
                                 'создаст ту же фикстуру. Вызовем метод напрямую в открытой Session и сравним все '
                                 'три числа по ключам.\n'
                                 '\n'
                                 '```python\n'
                                 'with Session(engine) as session:\n'
                                 '    print(SqlTaskRepository(session).statistics())\n'
                                 '```\n'
                                 '\n'
                                 'Когда прямой результат совпадёт с `all=3`, `open=1`, `done=2`, вставим в том же '
                                 'Python готовый контроль ниже. Он запустит сводку на пустой и смешанной таблицах, '
                                 'после завершения и удаления id 24, и напечатает ответ `/stats` на исходных трёх '
                                 'задачах. Контроль использует временный файл и тестовую подмену. Отдельный '
                                 'HTTP-сервер запускать не нужно.\n'
                                 '\n'
                                 '```python\n'
                                 "exec('''\n"
                                 'from os import environ\n'
                                 'from unittest.mock import patch\n'
                                 'from fastapi.testclient import TestClient\n'
                                 'from sqlalchemy import event\n'
                                 'from app.main import build_service\n'
                                 'from app.core.dependencies import get_planner\n'
                                 '\n'
                                 'def read_check(expected):\n'
                                 '    statements = []\n'
                                 '    def capture(connection, cursor, statement, parameters, context, many):\n'
                                 '        statements.append(statement.upper().split())\n'
                                 '    event.listen(engine, "before_cursor_execute", capture)\n'
                                 '    try:\n'
                                 '        with Session(engine) as session:\n'
                                 '            actual = SqlTaskRepository(session).statistics()\n'
                                 '    finally:\n'
                                 '        event.remove(engine, "before_cursor_execute", capture)\n'
                                 '    assert actual == expected, (actual, expected)\n'
                                 '    assert len(statements) == 1 and statements[0][0] == "SELECT", statements\n'
                                 '    sql = "".join(statements[0])\n'
                                 '    assert sql.count("COUNT(") == 3 and sql.count("CASEWHEN") == 2, sql\n'
                                 '    print(actual)\n'
                                 '\n'
                                 'def planner():\n'
                                 '    with Session(engine) as session:\n'
                                 '        yield build_service(repository=SqlTaskRepository(session),\n'
                                 '                            write_scope=session.begin)\n'
                                 '\n'
                                 'def check_cases():\n'
                                 '    try:\n'
                                 '        reset([])\n'
                                 '        read_check({"all": 0, "open": 0, "done": 0})\n'
                                 '        reset(rows)\n'
                                 '        read_check({"all": 3, "open": 1, "done": 2})\n'
                                 '        with Session(engine) as session:\n'
                                 '            service = build_service(repository=SqlTaskRepository(session),\n'
                                 '                                    write_scope=session.begin)\n'
                                 '            service.mark_done(24)\n'
                                 '            read_check({"all": 3, "open": 0, "done": 3})\n'
                                 '            service.delete_task(24)\n'
                                 '            read_check({"all": 2, "open": 0, "done": 2})\n'
                                 '        reset(rows)\n'
                                 '        with patch.dict(environ, {"JSON_PATH": str(Path(scratch.name) / '
                                 '"fixture.json")}):\n'
                                 '            from app.api import app\n'
                                 '        before = app.dependency_overrides.copy()\n'
                                 '        with TestClient(app) as client:\n'
                                 '            old_response = client.get("/stats")\n'
                                 '            app.dependency_overrides[get_planner] = planner\n'
                                 '            try:\n'
                                 '                result = client.get("/stats")\n'
                                 '                assert result.status_code == 200\n'
                                 '                assert result.json() == {"total": 3, "open": 1, "done": 2}\n'
                                 '                print(result.json())\n'
                                 '            finally:\n'
                                 '                app.dependency_overrides.clear()\n'
                                 '                app.dependency_overrides.update(before)\n'
                                 '            restored = client.get("/stats")\n'
                                 '            assert (restored.status_code, restored.json()) == '
                                 '(old_response.status_code, old_response.json())\n'
                                 '        print("Контроль пройден")\n'
                                 '    finally:\n'
                                 '        reset(rows)\n'
                                 '\n'
                                 'check_cases()\n'
                                 "''')\n"
                                 '```',
                         'requirements': ['В `SqlTaskRepository` реализован `statistics(self) -> dict[str, int]`. '
                                          'Запрос выполняется через уже переданную Session.',
                                          'Один агрегатный SELECT над `tasks` возвращает одну строку с именами '
                                          '`all`, `open`, `done`. `all` учитывает все задачи. Два условных COUNT '
                                          'учитывают открытые и завершённые соответственно. Список задач в Python '
                                          'не загружается.',
                                          'Значения прочитаны по именам и возвращены как словарь. На смешанном '
                                          'наборе это `all=3`, `open=1`, `done=2`, на пустом все три значения '
                                          'равны 0.',
                                          'Готовый контроль получает `all=3, open=0, done=3` после завершения 24 и '
                                          '`all=2, open=0, done=2` после удаления 24. На исходном наборе `/stats` '
                                          'сохраняет поля `total`, `open`, `done` со значениями 3, 1 и 2.'],
                         'result': 'Сначала прямой вызов показывает `all=3`, `open=1`, `done=2`. Затем готовый '
                                   'контроль заканчивается строкой «Контроль пройден». При ошибке сравните первое '
                                   'несовпавшее имя со статусом, который считает соответствующая колонка.',
                         'hint': 'В заметке из первой карточки видно, к какому итогу прибавился id выбранной '
                                 'задачи. Сверьте этот смысл с подписью и убедитесь, что неподходящее значение '
                                 'будет пропущено при подсчёте.',
                         'answer': {'explanation': 'Сохраним все строки для общего счёта. Каждому статусному '
                                                   'счётчику дадим свой условный столбец: номер подходит только '
                                                   'для нужного статуса.',
                                    'steps': ['В методе используем `TaskRow.id` для общего COUNT. В исходной схеме '
                                              'номер заполнен у каждой задачи. Он даёт вклад всех трёх строк, а '
                                              'глобальный WHERE по одному статусу убрал бы часть строк и из общего '
                                              'счёта.',
                                              'Построим два разных условных выражения. В этом варианте `.is_()` '
                                              'задаёт проверку SQL на логическое значение. Для нашего поля без '
                                              'NULL подходит и знакомое сравнение с True или False. Для открытых '
                                              'условие проверяет False и отдаёт номера `24, NULL, NULL`. Для '
                                              'завершённых условие проверяет True и отдаёт `NULL, 41, 58`. В обоих '
                                              'выражениях неподходящее значение пустое. С нулём вместо NULL '
                                              'счётчик тоже увидел бы эту ячейку.',
                                              'Применим COUNT к каждому выражению и подпишем колонки. Сопоставим с '
                                              'заметкой: id 41 попал в условное выражение с именем `done`. При '
                                              'обмене условиями или метками `open` покажет 2, `done` покажет 1, а '
                                              '`all` останется 3.',
                                              'Соберём три выражения в один SELECT без групп. Выполним его '
                                              'существующей Session и прочитаем одну именованную строку. В словарь '
                                              'передадим числа по ключам. В пустой таблице нет заполненных ячеек, '
                                              'поэтому строка содержит три нуля.',
                                              'Сохраним файл и выполним прямой вызов из условия в новом процессе. '
                                              'Затем готовый контроль меняет состояние 24: сначала 24 переходит из '
                                              'открытых в завершённые, потом исчезает. Для HTTP исходный набор '
                                              'создаётся заново, поэтому `total` снова 3.'],
                                    'code': 'from sqlalchemy import case, func, select\n'
                                            '\n'
                                            '# Внутри существующего SqlTaskRepository\n'
                                            'def statistics(self) -> dict[str, int]:\n'
                                            '    open_id = case((TaskRow.is_done.is_(False), TaskRow.id), '
                                            'else_=None)\n'
                                            '    done_id = case((TaskRow.is_done.is_(True), TaskRow.id), '
                                            'else_=None)\n'
                                            '    statement = select(\n'
                                            '        func.count(TaskRow.id).label("all"),\n'
                                            '        func.count(open_id).label("open"),\n'
                                            '        func.count(done_id).label("done"),\n'
                                            '    )\n'
                                            '    row = self.session.execute(statement).mappings().one()\n'
                                            '    return {"all": row["all"], "open": row["open"], "done": '
                                            'row["done"]}',
                                    'checks': ['Импортируйте `case` и `func` рядом с уже использованным `select`, '
                                               'затем поместите метод внутрь существующего класса в '
                                               '`project/app/DBase/repositories.py`.',
                                               "До HTTP прямой вызов возвращает `{'all': 3, 'open': 1, 'done': "
                                               '2}`. Если поменять метки `open` и `done`, сравнение по ключам '
                                               'покажет 2 и 1 в обратном порядке. Если заменить NULL нулём, оба '
                                               'счёта по статусам дадут 3.',
                                               'После запуска готового контроля проверьте последовательность '
                                               'чисел. Пустая (0, 0, 0), смешанная (3, 1, 2), завершение 24 (3, 0, '
                                               '3), удаление 24 (2, 0, 2). Тестовый ответ `/stats` содержит '
                                               '`total=3`, `open=1`, `done=2`.']}}],
              'result': 'На тестовых задачах проверен вклад выбранной строки и получена именованная сводка из '
                        'одного запроса.'}],
}
