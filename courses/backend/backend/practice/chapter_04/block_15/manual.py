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
    72: [
        {
            "title": "PATCH в тестовой SQLite",
            "task": "Продолжаем свой Planner. PUT уже находит задачу и записывает её в тестовую SQLite. Раздел «Что мы будем делать в практике» содержит архив «Скачать оснастку PATCH».",
            "tasks": [
                {
                    "title": "Сохраняем только поля из PATCH",
                    "task": "Из архива переносим tests/test_sql_patch_lab.py в tests/ своего проекта. Из каталога project в привычном окружении выполняем python -m pytest -q -s tests/test_sql_patch_lab.py -k start. В выводе PUT start видим HTTP 200, прежние id и теги, заголовок «Проба PUT» и ключ «проба put». Затем заменяем только метод PlannerService.patch_task в app/service.py целиком фрагментом patch_scope.fragment.txt, сохраняя отступ внутри класса.\n\nВ app/DBase/repositories.py сами пишем SqlTaskRepository.update. Готовый сервис уже передаёт ему полную проверенную Task. Смотрим, как PUT записывает её состояние по id, и используем подходящую часть существующего репозитория. Запускаем python -m pytest -q -s tests/test_sql_patch_lab.py. Сравниваем снимки до и после для задачи 120 и её соседа 121. У задачи 120 исходные title «Старый План», priority 4, is_done=True, теги [«внутр»] и ключ «старый план». Объясняем по выводу, откуда пришло False и почему пустой запрос дал ноль UPDATE.",
                    "requirements": [
                        "В SqlTaskRepository определён update(self, task: Task) -> Task | None. Он обновляет существующую строку с task.id в текущей Session, возвращает сохранённую Task, а при отсутствующей строке возвращает None. Подтверждение принадлежит подключённой области patch_task, открытой перед первым чтением.",
                        "PATCH /tasks/120 с {\"is_done\": false, \"priority\": null} даёт 200 и один UPDATE. Новое чтение показывает False, приоритет 4, прежний заголовок, ключ и теги. Публичный ответ содержит id, title, priority и is_done.",
                        "PATCH той же задачи с {} и отдельный PATCH с {\"title\": null} дают 200 и ноль UPDATE каждый. Новое чтение показывает состояние предыдущего запроса.",
                        "Переименование на «Новый План» даёт 200 и один UPDATE. Заголовок и ключ «новый план» сохранены. id, приоритет, флаг и теги прежние. Строка 121 совпадает со снимком до каждого запроса.",
                        "Пустой PATCH /tasks/999 даёт 404, запрос с приоритетом 6 даёт 422. Оба дают ноль UPDATE. После снятия подмены прежний PATCH проходит на временном JSON."
                    ],
                    "result": "Готовая проверка проходит. В новой Session флаг равен False, пустой PATCH даёт ноль UPDATE, а новое имя и его ключ читаются в SQLite.",
                    "hint": "Вернёмся к записи существующей строки в PUT и к схеме выбранных полей. Сначала определим, в какой ветви сервис вообще передаёт в репозиторий полностью собранную Task.",
                    "answer": {
                        "explanation": "Решение опирается на SQL-запись полного состояния, которую мы уже сделали для PUT. Сам сервис PATCH решил, какие значения взять из запроса, и оставил в Task прежние id и теги. Транзакция принадлежит сервису и охватывает поиск. Поэтому пустой запрос к неизвестному id получает 404 даже без записи.",
                        "steps": [
                            "Готовой проверкой start сверяем, что PUT записывает Task по id и новое чтение видит переименование. Этим проверяем вход для нового метода.",
                            "В app/service.py подставляем готовую область patch_task. Чтение, проверка и возможный update проходят в одной области, поэтому ответ об успехе следует за подтверждением.",
                            "В SqlTaskRepository добавляем update. Внутри репозитория переиспользуем прежнюю запись найденной строки. Метод replace принимает уже собранную Task, обновляет атрибуты строки, сохраняет согласованность ключа и преобразует строку обратно в Task.",
                            "В прогоне смотрим столбец UPDATE и снимки. Запрос с False меняет флаг. Для {} и null словарь изменений пуст, поэтому сервис после поиска сразу отдаёт прочитанную Task. Следующий запрос с новым именем обновляет технический ключ."
                        ],
                        "code": "def update(self, task: Task) -> Task | None:\n    return self.replace(task)",
                        "checks": [
                            "python -m pytest -q -s tests/test_sql_patch_lab.py выполняет входную проверку и сценарии PATCH.",
                            "Первые три PATCH дают соответственно UPDATE=1, UPDATE=0 и UPDATE=0, а переименование снова даёт UPDATE=1.",
                            "Неизвестный id возвращает 404, соседняя строка и теги совпадают с исходными снимками."
                        ]
                    }
                }
            ],
            "result": "Прежний HTTP PATCH выполняет частичное обновление в тестовой SQLite. Сохранение доказано чтением новой Session, no-op подтверждён счётчиком."
        }
    ],
    "72.1": [
        {
            "title": "Завершение задачи в Planner",
            "task": "Доработайте прежнюю команду done на своём Planner после практики частичного обновления. Временная база готовится из знакомой пары задач. После PATCH выбранная задача открыта, рядом есть контрольная строка с тегами.",
            "tasks": [
                {
                    "title": "Подключите завершение к общей SQL-записи",
                    "task": "Откройте каталог `project` своего Planner и найдите `PlannerService.mark_done` в `app/service.py`. В разделе «Что мы будем делать в практике» скопируйте открытый фрагмент «Старт для mark_done» вместо этого метода и сохраните блок «Проверка done» в `project/check_done_practice.py`. В готовой области записи самостоятельно решите, как ответить для открытой и уже завершённой задачи. Сохраните прежнюю реакцию на отсутствующий id. Запустите `python check_done_practice.py` из `project` и сравните два вызова для одной задачи и вызов для отсутствующего id. Сохраните строки `DONE_TASK` и `CONTROL_TASK` из успешного вывода.",
                    "requirements": [
                        "Код `PlannerService.mark_done` получает задачу и при изменении передаёт обычную Task общему `self.repository.update(task)` в той же области записи. Метод пользуется одним `write_scope`. Публичный `patch_task` и ручной `commit` внутри метода не вызываются.",
                        "Первый вызов для открытой задачи даёт «Задача выполнена.», один вызов общего `update` и один SQL `UPDATE`. В новой Session изменился только `is_done`: он стал `True`. Соседняя строка совпала с исходной.",
                        "Повторный вызов даёт «Задача выполнена.» при нуле вызовов `update` и нуле SQL `UPDATE`. Для отсутствующего id проверка видит «Задача не найдена.» и те же нулевые счётчики. В обоих случаях свежие строки сохраняются."
                    ],
                    "result": "Проверка завершилась сообщением «Проверка done прошла» и напечатала `DONE_TASK` и `CONTROL_TASK` с пятью полями каждой задачи. Проверка исходника подтверждает одну область записи.",
                    "hint": "Вернитесь к схеме «Что происходит после команды закрытия». В какой ветви карточка уже содержит нужное состояние? Если в отчёте флаг совпал, а счётчик вызовов ненулевой, сравните два наблюдения из раздела «Как увидеть лишний вызов записи».",
                    "answer": {
                        "explanation": "Для завершённой задачи уже выполнено требование команды, поэтому возвращаем её сразу. Для открытой `task.mark_done()` меняет объект в памяти. `repository.update(task)` сохраняет изменённую задачу и возвращает сохранённый результат. Выход из `with` завершает транзакцию перед возвратом к обработчику.",
                        "steps": [
                            "Получаем Task прежним `get_task` внутри области записи. Для отсутствующего id этот вызов уже поднимает `TaskNotFoundError`.",
                            "Проверяем `task.is_done`. Если флаг уже истинный, возвращаем найденную Task до обращения к общему update.",
                            "Изменяем флаг на открытой Task и передаём объект существующему `repository.update`. Если репозиторий вернул `None`, пользуемся прежней ошибкой отсутствия. Иначе возвращаем полученную Task."
                        ],
                        "code": "    def mark_done(self, task_id):\n        with self.write_scope():\n            task = self.get_task(task_id)\n            if task.is_done:\n                return task\n\n            task.mark_done()\n            stored = self.repository.update(task)\n            if stored is None:\n                self._raise_task_not_found(task.id)\n            return stored",
                        "checks": [
                            "У первого вызова счётчики равны 1 и 1, у повторного и отсутствующего 0 и 0.",
                            "Если повтор вызвал update, даже при нуле SQL-команд проверка отметит лишнее обращение к репозиторию.",
                            "Свежая Session видит True у цели и прежние данные соседа. В выводе есть две строки для переноса."
                        ]
                    }
                }
            ],
            "result": "Прежняя команда done работает в тестовом SQL-сервисе Planner. Первый вызов сохраняет завершение, повтор обходится без записи."
        }
    ],
    75: [
        {
            "title": "Перевести штатные API и CLI на одну SQLite",
            "task": (
                "Обычные API и CLI вашего Planner пока работают с JSON. Вы уже проверили перенос на пробной базе. "
                "Откройте отдельную локальную учебную копию своего Planner и возьмите оба файла из блока «Готовые материалы» в теории. "
                "Выполняйте команды из её project/ в привычном окружении. Все записи и остановки процессов относятся к этой учебной копии."
            ),
            "tasks": [
                {
                    "title": "Переключить оба входа после сверки актуального снимка",
                    "task": (
                        "Подготовьте отдельный синтетический JSON командой seed с новым именем прогона. "
                        "В обоих учебных терминалах задайте напечатанный JSON_PATH и подтвердите адрес командой source. "
                        "Запустите обычные API и CLI из памятки. Через add создайте задачу «Сверить карту склада» с приоритетом 3, "
                        "запишите выданный id и прочитайте эту задачу в API. Закройте CLI и API, затем командой prepare снимите архив "
                        "и сверьте контрольный id. Запустите напечатанную команду импорта и compare для того же прогона. "
                        "Решите, можно ли переходить к смене сборок. Отдельно объясните решение для отчёта из теории, "
                        "где набор id одинаков, а у коврика потерян тег.\n\n"
                        "После полной сверки сами подключите выданные SQL-ресурсы в app/api.py, app/core/dependencies.py и app/main.py. "
                        "Сохраните прежний вариант изменяемых строк в черновике редактора. Сразу подготовьте обе точки входа, "
                        "задайте напечатанный DATABASE_URL и запустите готовый smoke в этом же терминале. "
                        "Запишите в data/cutover/<имя прогона>/result.md, что прочитали разные процессы и что показали отпечатки JSON. "
                        "Отметьте, где нужно остановиться при сбое до переключения и какой источник содержит актуальные данные после новой записи."
                    ),
                    "requirements": [
                        "API и CLI до перехода используют один отдельный синтетический tasks.json. Контрольная задача прочитана через API, а её фактический id найден в архиве, снятом после завершения обоих учебных процессов.",
                        "Init подтвердил пустую цель. До новых записей compare показывает тот же набор id, совпадение title, priority, is_done, tags и title_normalized, а также одинаковые отпечатки исходника и архива. Вывод про потерянный тег называет поле и id и объясняет, почему прежние входы остаются на JSON.",
                        "Штатный API получает PlannerService на Session текущего запроса. Штатный CLI получает отдельный SQL-контекст на команду. Обе обычные сборки берут один абсолютный DATABASE_URL из настроек. Маршруты и команды по-прежнему используют прежний сервисный договор.",
                        "Обычный API создаёт открытую задачу с положительным свободным id. CLI видит этот id и завершает задачу. Свежий GET и перезапущенный API возвращают is_done=true. В сводке total и done увеличились на 1, open сохранился.",
                        "Отчёт smoke подтверждает неизменность исходного, архивного и контрольного JSON. В result.md сохранены путь источника, архив, общий URL, полная сверка, фактический id, чтения, отпечатки и решение о восстановлении. После первой новой записи актуальное состояние находится в SQLite.",
                    ],
                    "result": (
                        "Два обычных входа работают на одной SQLite. В result.md записано, что свежая задача видна через CLI и новый API-процесс, "
                        "а JSON остался прежним."
                    ),
                    "hint": (
                        "Вернитесь к карте трёх файлов в разделе «Как открыть одну базу из двух штатных входов». "
                        "Проследите, откуда обычный запрос получает Session и откуда команда получает контекст. "
                        "Если ошибка возникла раньше, найдите в отчёте тот же id и первое несовпавшее поле."
                    ),
                    "answer": {
                        "explanation": (
                            "Переключение начинается с проверенного состояния. Штатный JSON фиксируется после остановки учебных писателей. "
                            "Сверка независимой Session подтверждает весь снимок. Затем оба обычных входа направляются на одну базу, "
                            "а короткие Session сохраняют прежние границы каждого действия."
                        ),
                        "steps": [
                            "В учебной копии создаём seed для свободного имени. В обоих PowerShell задаём напечатанный JSON_PATH, выполняем source и запускаем обычные входы. Через CLI добавляем контрольную задачу. Успешный GET с полученным id подтверждает, что оба входа смотрят на выбранный JSON.",
                            "Завершаем CLI через exit, останавливаем свой API через Ctrl+C. В prepare сверяем наличие только что замеченного id, отпечатки и пустую схему. Выполняем напечатанную команду импорта, затем compare. При потере тега у id 11 в примере не меняем сборки, даже если id и число строк совпали.",
                            "В app/api.py сохраняем SQL-фабрику на app.state. В app/core/dependencies.py берём Session из get_db и собираем сервис запроса. Определение get_planner располагаем после get_db и до PlannerDep. Имя get_planner остаётся точкой, к которой обращаются маршруты.",
                            "В app/main.py создаём фабрику для settings.database_url и передаём в знакомый run функцию получения контекста. Готовый sql_service_context открывается при команде. Оба изменения вносим при остановленных входах.",
                            "Задаём напечатанный DATABASE_URL, запускаем smoke и переносим наблюдения в result.md. Сверяем новый id, чтение CLI, статус из перезапущенного API и отпечатки. Если расхождение возникло до смены, оба прежних входа можно восстановить на учебном JSON. После новой SQL-записи останавливаем учебные процессы и сохраняем SQLite для отдельного восстановления.",
                        ],
                        "code": (
                            "# app/api.py: импорт в начале файла, присваивание вместо сборки JSON-сервиса\n"
                            "from app.cutover_wiring import make_session_factory\n"
                            "\n"
                            "app.state.session_factory = make_session_factory(settings.database_url)\n"
                            "\n"
                            "# app/core/dependencies.py: импорт в начале, get_planner после get_db\n"
                            "from app.cutover_wiring import make_http_planner\n"
                            "\n"
                            "def get_planner(db: Session = Depends(get_db)) -> PlannerService:\n"
                            "    return make_http_planner(db)\n"
                            "\n"
                            "# app/main.py: импорт в начале, запуск внутри существующей main\n"
                            "from app.cutover_wiring import make_cli_context, make_session_factory\n"
                            "\n"
                            "def main():\n"
                            "    settings = load_settings()\n"
                            "    try:\n"
                            "        factory = make_session_factory(settings.database_url)\n"
                            "        run(context_factory=make_cli_context(factory, sql_service_context))\n"
                            "    except StorageError as error:\n"
                            "        print(f'Не удалось выполнить операцию с задачами. Приложение остановлено: {error}')\n"
                        ),
                        "checks": [
                            "Смена JSON_PATH на другой файл даёт отказ source и prepare до чтения его записей. Архив с пропущенным контрольным id до смены входов не принимается.",
                            "У отчёта с теми же id и потерянным тегом результат сравнения отрицательный. Для отдельного пустого примера две стороны действительно пусты, и сравнение даёт ноль строк.",
                            "Если один вход остался на JSON, smoke видит контрольную строку через этот вход или пропажу импортированной строки и останавливается до контрольной записи.",
                            "Успешный smoke даёт новый положительный id, видимый из CLI, завершённый статус в свежем API и после его перезапуска, а все три JSON сохраняют исходные отпечатки.",
                        ],
                    },
                },
            ],
            "result": "Подготовлена и проверена одна SQLite для штатных API и CLI, сохранены учебный архив и отчёт о переходе.",
        },
    ],
    "74.1": [
        {
            "title": "Пробный перенос задач в одну SQLite",
            "task": "В своём Planner проведём одну репетицию. Оснастку rehearse_snapshot.py берём целиком из блока «Подготовка к запуску» в теории. Сохраняем её в корне project/ и выполняем команды из этой папки в окружении, где запускаются тесты Planner.",
            "tasks": [
                {
                    "title": "Подтвердить перенос и работу обоих интерфейсов",
                    "task": "Выберем новое имя внутри data/rehearsal/ и запустим python rehearse_snapshot.py prepare с этим путём без --demo. Получим пути к снимку и SQLite. Проверим справку импортёра и сами составим команду из двух путей. Затем запустим compare с тем же каталогом. На совпавшем наборе повторим команду импорта, увидим ожидаемый отказ и ещё раз сверим строки. После успешной сверки запустим smoke с тем же каталогом. Запишем наблюдения в result.md внутри каталога прогона и укажем, прошла ли репетиция. При неожиданном сбое найдём первую проверку, сравним ожидаемый и фактический результат. Для нового прогона возьмём свободное имя.",
                    "requirements": [
                        "В свежем каталоге data/rehearsal/<имя>/ лежат синтетический snapshot.json и planner-test.sqlite3. Вывод подготовки подтверждает явный init и показывает абсолютные пути. Импорт с --source и --database сообщает о двух загруженных записях.",
                        "Перед пробными записями compare подтверждает полный набор id 17 и 24 и значения title, priority, is_done, tags и title_normalized. У id 17 сохранён тег «склад», у старой записи id 24 tags равен []. При любом отличии строки прогон до smoke останавливается.",
                        "Повтор импорта в заполненную таблицу отклоняется. Следующее чтение подтверждает те же строки и байты снимка.",
                        "Smoke показывает один абсолютный путь SQLite. CLI find читает фактический id, созданный HTTP POST, а свежий HTTP GET видит is_done=true после CLI done. HTTP-сводка показывает total=3, open=1, done=2.",
                        "Готовый набор подтверждает прежние ответы маршрутов и команд, чтение штатного JSON в контрольной сборке и неизменность исходных байтов снимка и рабочего JSON.",
                        "В result.md указаны пути, результат сравнения, ожидаемый отказ, фактический id и оба наблюдения API/CLI, состояние JSON и решение по репетиции. При сбое записано первое расхождение."
                    ],
                    "result": "В тестовом каталоге есть SQLite с пробным состоянием и result.md с доказательствами готовности либо с первым расхождением. Штатный Planner продолжает работать на JSON.",
                    "hint": "Вернёмся к схеме и сверим, какой аргумент указывает на уже созданный файл. Если расходятся данные, найдём поле у того же id. При ошибке smoke посмотрим, какой интерфейс последним подтвердил сохранённое состояние.",
                    "answer": {
                        "explanation": "Init создаёт пустую таблицу по адресу DATABASE_URL. Импортёр читает конкретный снимок и пишет в уже созданный SQLite-файл. Compare читает строки отдельно от импорта, поэтому находит отличие даже при двух строках с обеих сторон. Два чтения после записей подтверждают видимость изменения из разных ресурсов.",
                        "steps": [
                            "Из project/ запускаем prepare в свободном каталоге и берём два пути из его вывода. Формируем команду по справке импортёра.",
                            "После успешного импорта первым compare сверяем полный набор. Для id 17 видим «Сверить склад 100%», приоритет 3, false и [«склад»]. Для id 24 видим «Убрать чехол», приоритет 1, true и пустой список тегов. Служебные имена записаны в нижнем регистре.",
                            "Повторяем команду импорта из той же папки. Сообщение про непустую tasks ожидаемо, потому что первый импорт уже записал данные. Следующий compare показывает, что отказ не изменил записи.",
                            "После smoke переносим в result.md фактические путь и id. При успехе отмечаем чтение новой задачи через CLI, затем её завершение, видимое через HTTP. Смотрим итог прежних маршрутов и проверку исходных файлов. При сбое указываем проверку, ожидание и наблюдение."
                        ],
                        "code": "python rehearse_snapshot.py prepare \"data/rehearsal/run-a\"\npython -m app.DBase.import_tasks --help\npython -m app.DBase.import_tasks --source \"data/rehearsal/run-a/snapshot.json\" --database \"data/rehearsal/run-a/planner-test.sqlite3\"\npython rehearse_snapshot.py compare \"data/rehearsal/run-a\"\n\n# Повтор импорта ожидаемо завершится отказом.\npython -m app.DBase.import_tasks --source \"data/rehearsal/run-a/snapshot.json\" --database \"data/rehearsal/run-a/planner-test.sqlite3\"\npython rehearse_snapshot.py compare \"data/rehearsal/run-a\"\npython rehearse_snapshot.py smoke \"data/rehearsal/run-a\"",
                        "checks": [
                            "В новом запуске импорт сообщает о двух задачах. Compare показывает обе строки и полное совпадение.",
                            "Повторный импорт возвращает отказ из-за непустой таблицы. Compare после него снова проходит.",
                            "При потере тега у id 17 число задач остаётся равным двум, а compare сообщает отличие tags. При таком отчёте пробные записи не начинаются.",
                            "След smoke содержит оба направления, сводку 3/1/2, сохранённый договор и совпавшие байты обоих JSON. Пробная база остаётся в каталоге репетиции."
                        ]
                    }
                }
            ],
            "result": "Собственный пробный прогон даёт решение о готовности с наблюдаемыми доказательствами до и после тестовых изменений."
        }
    ],
    73: [
        {
            "title": "Получаем нужные задачи из SQLite",
            "task": "В своём Planner продолжите SQL-репозиторий. Прежний список уже принимает статус, направление и лимит. Добавьте SQL-выборку с теми же настройками и подтвердите её на отдельной временной базе.",
            "tasks": [
                {
                    "title": "Одна выборка в SqlTaskRepository",
                    "task": "Откройте «Готовая проверка выборки» в теории и сохраните весь блок в tests/test_lesson_sql_selection.py. Если имя занято, возьмите свободное имя вида test_*.py в этой же папке и используйте его в командах. Из корня Planner в своём тестовом окружении запустите `python -m pytest -q -s tests/test_lesson_sql_selection.py -k test_input_sql_selection`. Код проверит знакомые операции чтения и записи во временной базе. В выводе будут три задачи по возрастанию id: завершённая «Закрыть счёт» с тегом «архив», открытая «Забрать заказ» с тегом «город» и открытая «Выбрать обложку» с тегом «макет». Назовём их id A, B и C. Выпишите ожидаемый id для запроса одной открытой задачи по возрастанию. Затем самостоятельно напишите select в классе SqlTaskRepository в app/DBase/repositories.py. Запустите `python -m pytest -q -s tests/test_lesson_sql_selection.py` и объясните по записанному запросу, почему лимит оставляет первую подходящую задачу.",
                    "requirements": [
                        "В SqlTaskRepository определён метод select(self, is_done=None, sort_desc=False, limit=None). Он выполняет одну SQL-выборку. Для is_done=None статус не ограничен, False выбирает открытые задачи, True выбирает завершённые.",
                        "Порядок задаётся в SQL по id: по возрастанию при sort_desc=False и по убыванию при True. Лимит применяется к подходящим строкам в заданном порядке. Для limit=None SQL-лимит отсутствует.",
                        "Из выбранных строк получен список доменных Task с исходными полями и тегами. При отсутствии строк получен пустой список. Чтение использует переданную Session, не закрывает её и не делает запись.",
                        "Локальная проверка подтверждает: открытые без лимита дают [B, C], завершённые дают [A], все без лимита по возрастанию дают [A, B, C]. Лимит в одну строку для открытых даёт [B] по возрастанию и [C] по убыванию. Без фильтра по возрастанию тот же лимит даёт [A].",
                        "Прежний GET /tasks на временной SQL-сборке сохраняет четыре публичных поля и лимит по умолчанию 10. Явные 1 и 50 принимаются, 0 и 51 дают 422. На наборе из 12 задач прямой сервис с limit=None и команда CLI list возвращают полный список."
                    ],
                    "result": "Обе команды pytest проходят. При чтении одной открытой задачи по возрастанию показан id B и один SELECT с условием по статусу, порядком по id и лимитом. Проверки подтверждают теги и прежние ответы сервиса, HTTP и CLI.",
                    "hint": "Вернитесь к схеме с билетами. Выпишите id задач, которые подходят по статусу, найдите первую в нужном порядке и сравните с выводом. Если вернулась A или список пуст, проверьте условие и лимит в напечатанном SELECT.",
                    "answer": {
                        "explanation": "Условие, порядок и лимит добавляются к одному выражению, которое SQLite выполняет при чтении. Для False условие нужно, поэтому проверяем наличие значения через is not None. При открытом фильтре A не участвует в результате и первый слот занимает B.",
                        "steps": [
                            "В SqlTaskRepository начните с выражения select(TaskRow), как в существующем list. Оно описывает выборку объектов из таблицы задач.",
                            "Если передан статус, добавьте условие WHERE и сохраните новое выражение. False попадает в эту ветку и выбирает открытые задачи.",
                            "Задайте порядок по TaskRow.id в нужном направлении. Добавьте ограничение только при переданном limit.",
                            "Выполните выражение один раз, преобразуйте полученные строки через task_from_row внутри открытой Session и верните список.",
                            "Запустите готовый файл pytest. Сравните id B с веткой, где фильтр не применился и вернулась бы A, и с ранним лимитом, после которого отбор оставил бы пустой список."
                        ],
                        "code": "def select(self, is_done=None, sort_desc=False, limit=None):\n    statement = select(TaskRow)\n    if is_done is not None:\n        statement = statement.where(TaskRow.is_done == is_done)\n\n    order = TaskRow.id.desc() if sort_desc else TaskRow.id.asc()\n    statement = statement.order_by(order)\n    if limit is not None:\n        statement = statement.limit(limit)\n\n    rows = self.session.scalars(statement).all()\n    return [task_from_row(row) for row in rows]",
                        "checks": [
                            "В обычном и различающем случаях id, поля и теги совпадают с ожиданиями. Пустая база даёт [].",
                            "В диагностике различающего вызова выполнен один SELECT с WHERE, ORDER BY и LIMIT. При обоих None остаётся ORDER BY без WHERE и LIMIT.",
                            "GET сохраняет прежние поля и границы. Сервис без лимита и CLI показывают все 12 задач."
                        ]
                    }
                }
            ],
            "result": "SQL-репозиторий отдаёт прежний список Planner, а фильтр, порядок и лимит выполняет SQLite."
        }
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

    74: [
        {
            "title": "Session на время команды",
            "task": "В своём Planner мы уже можем запускать привычный CLI на JSON и собрать SQL-сервис для изолированного опыта. В разделе «Готовые материалы» возьмём стартовый хвост app/cli.py и файл check_cli_session.py. Первый блок заменяет участок от def execute_command( до конца файла. Второй сохраняем рядом с каталогом app. Начнём в project/ с команды python check_cli_session.py --baseline. Журнал покажет одну Session на весь цикл, хотя вход включает только опечатку и exit.",
            "tasks": [
                {
                    "title": "Перенести границу Session и проверить команду",
                    "task": "В app/cli.py изменим связь run с execute_command и область вызова существующего обработчика. Передадим из цикла средство получить сервис для отдельной команды и сами определим, где открыть и закрыть контекст. Затем из project/ запустим python check_cli_session.py. Сверим add задачи «Проверить проект» с приоритетом 3 и чтение этого же id через find. Проверим поиск отсутствующего id, опечатку fiind, exit, отдельный сбой во время запроса номера задачи и прежний запуск на временном JSON. Оснастка сама подаёт ввод, печатает журнал и показывает, что сохранено.",
                    "requirements": [
                        "В app/cli.py цикл run передаёт готовую фабрику единственному существующему диспетчеру. Меню появляется при закрытых Session, а прежний вход run(service) обслуживает JSON.",
                        "Порядок распознавания, обработчики и сообщения CLI сохранены. Опечатка fiind показывает «Неизвестная команда.», exit заканчивает цикл. Ни один из этих двух вводов не даёт открытия Session.",
                        "Распознанный обработчик получает сервис из собственного контекста. Ресурс закрывается при успехе, при поиске отсутствующего id и при контролируемом исключении. Причина исключения видна самопроверке.",
                        "После add следующий find с пробелами и смешанным регистром читает сохранённую задачу по тому же id. Журнал показывает разные метки Session. Поиск отсутствующего id даёт «Задача не найдена.».",
                        "Обычный запуск умеет добавить и прочитать задачу из указанного временного JSON. Рабочий адрес и данные проекта сохраняются."
                    ],
                    "result": "python check_cli_session.py проходит проверки. До каждого приглашения меню предыдущая Session закрыта. Запись видна в новой Session, а опечатка и выход не открывают ресурса.",
                    "hint": "Посмотрим на ленту проката и на два разных сообщения об ошибке. В какой момент команда уже выбрана и к какому приглашению её ресурс должен быть закрыт?",
                    "answer": {
                        "explanation": "В прокате право читать ведомость получает оформленное действие. Если поиск ничего не нашёл, Session всё равно была нужна и закрывается при выходе. В Planner это же различие определяет точку владения: ошибка поиска возникает внутри действия, а опечатка обнаруживается при распознавании.",
                        "steps": [
                            "Сохраняем выбор фабрики в run и оставляем ожидание ввода снаружи контекста. Поэтому ожидание нового ввода больше не удерживает Session.",
                            "Из цикла передаём саму фабрику в execute_command. В этом вызове сервиса ещё нет, поэтому передаём None в позицию service.",
                            "Сохраняем ранние возвраты и таблицу команд. Выбранный обработчик вызываем внутри одного with и передаём ему выданный сервис. Запись по-прежнему подтверждает write_scope сервиса.",
                            "Запускаем python check_cli_session.py. В первых двух действиях сравниваем выведенный id и разные пары open/close. Отдельно смотрим последний вывод каждого пограничного сценария."
                        ],
                        "code": "def choose_context_factory(service, context_factory):\n    from contextlib import nullcontext\n\n    if context_factory is not None:\n        return context_factory\n    return lambda: nullcontext(service)\n\n\ndef execute_command(service, command, read=input, write=print, *, context_factory=None):\n    normalized_command = command.strip().lower()\n    if normalized_command == \"exit\":\n        return False\n\n    handlers = {\n        \"add\": handle_add,\n        \"list\": handle_list,\n        \"find\": handle_find,\n        \"done\": handle_done,\n        \"delete\": handle_delete,\n        \"search\": handle_search,\n        \"stats\": handle_stats,\n    }\n    handler = handlers.get(normalized_command)\n    if handler is None:\n        write(\"Неизвестная команда.\")\n        return True\n\n    context_factory = choose_context_factory(service, context_factory)\n    with context_factory() as command_service:\n        handler(command_service, read=read, write=write)\n    return True\n\n\ndef run(service=None, read=input, write=print, *, context_factory=None):\n    context_factory = choose_context_factory(service, context_factory)\n    while True:\n        show_menu(write)\n        command = read(\"Команда: \")\n        keep_running = execute_command(\n            None, command, read=read, write=write, context_factory=context_factory\n        )\n        if not keep_running:\n            break",
                        "checks": [
                            "У add и следующего find общий id и разные пары open/close. Перед приглашениями меню журнал показывает «menu: нет».",
                            "Отсутствующий id открывает и закрывает третью Session. Для fiind и exit открытий нет.",
                            "При контрольном исключении видны одно открытие, одно закрытие и успешная проверка исключения.",
                            "Последняя строка проверки сообщает, что обычный запуск сохранил и показал задачу через временный JSON."
                        ]
                    }
                }
            ],
            "result": "Режим учебного SQL CLI использует отдельный ресурс на команду. Локальный прогон показывает сохранённую запись, освобождение Session и сохранность штатной JSON-сборки."
        }
    ],
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
    '73.2': [
        {
            "title": "Считаем задачи одним SQL-запросом",
            "task": "Продолжаем работу в своём Planner после переноса буквального поиска. Статистика должна отражать всё содержимое SQLite. Сохраним прежние ответы сервиса, HTTP и CLI.",
            "result": "Сводку вычисляет один SELECT. Пустая база даёт три нуля, а после завершения и удаления задачи интерфейсы показывают актуальные числа.",
            "tasks": [
                {
                    "title": "Добавить сводку в SQL-репозиторий",
                    "task": "Откроем `project/app/DBase/repositories.py` в своём Planner и найдём `SqlTaskRepository`. Мы сами добавим метод `statistics()` и нужные импорты. В одном запросе пусть SQLite посчитает все задачи, открытые и завершённые. Прочитаем одну строку результата и вернём три числа сервису.\n\nВ теории откроем блок «Готовый файл проверки SQL-статистики» и скопируем весь файл в `project/tests/test_sql_statistics.py`. Он сам создаёт временную SQLite и подготавливает сценарии. Из `project/`, в окружении Planner, запустим `python -m pytest -q tests/test_sql_statistics.py -k repository`. Затем запустим `python -m pytest -q tests/test_sql_statistics.py`, чтобы сверить ответы сервиса, HTTP и CLI до и после знакомых операций.\n\nСчётчики в порядке «все / открытые / завершённые» составляют `0 / 0 / 0` на пустой базе и `5 / 4 / 1` на смешанной, где завершена только задача 14. После завершения задачи 21 ожидаем `5 / 3 / 2`. После удаления задачи 14 ожидаем `4 / 3 / 1`. Объясним, почему SQLite возвращает одну строку с тремя нулями даже при пустой таблице.",
                    "requirements": [
                        "Метод `SqlTaskRepository.statistics(self) -> dict[str, int]` выполняет один агрегатный `SELECT` для всей таблицы. В результате одна строка и три целых числа. Условные счётчики используют `COUNT` и `CASE`.",
                        "Метод использует уже открытую Session. За время подсчёта отсутствуют дополнительные запросы, загрузка списка, создание объектов `Task`, запись, `commit()` и `close()`.",
                        "Репозиторий и сервис отдают словарь с ключами `all`, `open`, `done`. `GET /stats` отвечает 200 с ключами `total`, `open`, `done`. CLI сохраняет прежние строки и долю завершённых, включая `0.0%` на пустой базе.",
                        "Оба локальных запуска проходят. После done и delete свежая Session видит итог по сохранённому состоянию. Штатный запуск остаётся на JSON."
                    ],
                    "hint": "Вернёмся к матрице вкладов заказов. Сверим, какие значения получает каждый условный счётчик и в каком порядке стоят числа. Если числа верные, но ключи расходятся, проследим путь сводки от сервиса до HTTP.",
                    "result": "Заметим в выводе успешный запуск проверок репозитория и всего файла. По своему методу объясним одну строку результата на пустой таблице.",
                    "answer": {
                        "explanation": "SQLite вычисляет один набор итогов по всей таблице. Условное выражение даёт id подходящей строки и NULL для остальных, поэтому COUNT учитывает только подходящие строки. Даже при пустом наборе возвращается одна строка итогов. Репозиторий сохраняет имена ключей сервиса, а HTTP использует готовое преобразование all в total.",
                        "steps": [
                            "В существующем SqlTaskRepository добавим новый метод статистики и объединим нужные импорты с уже имеющимися.",
                            "Зададим общий COUNT по id. Для двух остальных столбцов отдельно выразим условия статуса через CASE и подсчитаем идентификаторы подходящих задач.",
                            "Выполним выражение в выданной репозиторию Session. Прочитаем одну строку, распакуем значения в том же порядке и вернём прежний словарь.",
                            "Запустим проверку репозитория и полный сценарий. Сопоставим выход с числами для пустой и смешанной баз, завершения и удаления."
                        ],
                        "code": "# project/app/DBase/repositories.py\n# Добавляем func и case к импортам sqlalchemy.\nfrom sqlalchemy import case, func, select\n\n# Фрагмент существующего класса. Сохраняем его остальные методы.\nclass SqlTaskRepository:\n    def statistics(self) -> dict[str, int]:\n        statement = select(\n            func.count(TaskRow.id),\n            func.count(case((TaskRow.is_done.is_(False), TaskRow.id))),\n            func.count(case((TaskRow.is_done.is_(True), TaskRow.id))),\n        )\n        total, opened, done = self.session.execute(statement).one()\n        return {\"all\": total, \"open\": opened, \"done\": done}",
                        "checks": [
                            "Локальная проверка репозитория подтверждает один SQL-запрос, нули без задач и разницу между открытыми и завершёнными задачами.",
                            "Полная проверка подтверждает все четыре состояния, поле total в HTTP и прежние строки CLI.",
                            "В теле метода только чтение. Управление Session остаётся у вызывающего кода."
                        ]
                    }
                }
            ]
        }
    ],
}
