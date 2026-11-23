from typing import Any

# Пустые слоты проектной практики по паспортам блока 15.
MANUAL_PRACTICE: dict[int | str, list[dict[str, Any]]] = {
    71: [
        {
            "title": "Создаём и заменяем задачу в SQLite",
            "task": "Продолжите Planner, в котором SQL-репозиторий уже умеет читать задачи. Добавьте "
                    "создание и замену. Сервис будет отвечать за транзакцию, а репозиторий за работу со строкой.",
            "tasks": [
                {
                    "title": "Подключаем транзакцию записи",
                    "task": "В разделе «Когда можно сказать „сохранено“» разберите, когда завершается блок "
                            "записи. Затем посмотрите, как в примере с проектором `writer.begin` попадает "
                            "в функцию, которая выполняет изменение.\n\n"
                            "В `app/service.py` добавьте в `PlannerService` необязательный параметр "
                            "`write_scope`. Оберните прежнюю работу `add_task` и `replace_task` в эту "
                            "область. В `app/main.py` проведите параметр через `build_service` и подключите "
                            "его в существующем `sql_service_context`.",
                    "requirements": [
                        "Вызовы без `write_scope` выполняют прежний код с `nullcontext`.",
                        "У создания и замены транзакция охватывает всю операцию, включая первый поиск при замене.",
                        "SQL-репозиторий и метод, открывающий транзакцию, используют одну Session.",
                    ],
                    "result": "SQL-сборка может подтвердить изменение до возврата из сервиса или откатить "
                              "его при исключении.",
                    "hint": "Вспомните, чем в примере отличались передача `writer.begin` и вызов `writer.begin()`.",
                    "answer": {
                        "explanation": "Сборка знает, какую Session использовать. Сервис знает, когда "
                                       "началась и когда закончилась операция. Поэтому сборка передаёт метод, "
                                       "а сам сервис вызывает его вокруг всей работы.",
                        "steps": [
                            "Добавляем именованный аргумент в конструктор после `repository`. Сохраняем "
                            "переданную функцию, а при None используем `nullcontext`. Поэтому прежним "
                            "вызовам сервиса можно обойтись без нового аргумента.",
                            "В `add_task` открываем область перед `create`; в `replace_task` перед "
                            "`get_task`. Старый код замены, включая обработку промаха и перенос меток, "
                            "остаётся внутри. Если открыть область после поиска, SQLAlchemy может уже "
                            "автоматически начать транзакцию.",
                            "В обеих ветках `build_service` передаём аргумент конструктору. В "
                            "`sql_service_context` связываем репозиторий и транзакцию с уже открытой "
                            "Session. Передаём `session.begin` без скобок. Когда сервис выходит из "
                            "`with`, SQLAlchemy подтверждает успешное изменение или откатывает его при исключении.",
                        ],
                        "code": (
                            "# app/service.py: новые части существующего класса\n"
                            "from contextlib import nullcontext\n"
                            "\n"
                            "# В конце __init__(self, storage=None, *, repository=None, write_scope=None):\n"
                            "        self.write_scope = nullcontext if write_scope is None else write_scope\n"
                            "\n"
                            "    def add_task(self, title, priority):\n"
                            "        with self.write_scope():\n"
                            "            return self.repository.create(title, priority)\n"
                            "\n"
                            "    def replace_task(self, task_id, *, title, priority, is_done):\n"
                            "        with self.write_scope():\n"
                            "            current = self.get_task(task_id)\n"
                            "            updated = Task(\n"
                            "                id=current.id, title=title, priority=priority,\n"
                            "                is_done=is_done, tags=current.tags.copy(),\n"
                            "            )\n"
                            "            stored = self.repository.replace(updated)\n"
                            "            if stored is None:\n"
                            "                self._raise_task_not_found(current.id)\n"
                            "            return stored\n"
                            "\n"
                            "# app/main.py: добавьте write_scope=None к именованным аргументам build_service.\n"
                            "# Ветка готового репозитория:\n"
                            "        return PlannerService(repository=repository, write_scope=write_scope)\n"
                            "# Ветка обычного JSON-хранилища:\n"
                            "    return PlannerService(storage, write_scope=write_scope)\n"
                            "\n"
                            "@contextmanager\n"
                            "def sql_service_context(session_factory):\n"
                            "    with session_factory() as session:\n"
                            "        yield build_service(\n"
                            "            repository=SqlTaskRepository(session),\n"
                            "            write_scope=session.begin,\n"
                            "        )"
                        ),
                    },
                },
                {
                    "title": "Создаём задачу с номером от SQLite",
                    "task": "Возьмите за ориентир пример с выдачей стойки из раздела «Когда появляется id "
                            "и что видит читатель». В `app/DBase/repositories.py` добавьте "
                            "`create(self, title, priority)` в `SqlTaskRepository`. Метод получает "
                            "название и приоритет, а возвращает обычную `Task` с созданным id.",
                    "requirements": [
                        "Строка содержит очищенный заголовок, приоритет, статус False, пустые метки "
                        "и заголовок в нижнем регистре в `title_normalized`.",
                        "Id назначает SQLite. Метод получает его после отправки INSERT и преобразует "
                        "строку в `Task`.",
                        "Репозиторий использует переданную Session. Транзакцией управляет сервис.",
                    ],
                    "result": "SQL-сборка POST может вернуть созданную задачу с её новым id.",
                    "hint": "На каком шаге у стойки появился номер? Вспомните функцию преобразования строки "
                            "в задачу, которой уже пользуется метод get.",
                    "answer": {
                        "explanation": "До вставки номера ещё нет. После `flush()` SQLAlchemy получает "
                                       "его от SQLite, и мы можем собрать `Task` для ответа.",
                        "steps": [
                            "Например, из «  Купить ЧАЙ  » и приоритета 3 получаем заголовок «Купить ЧАЙ», "
                            "поисковую копию «купить чай», статус False и отдельный пустой список меток. "
                            "Используем знакомые валидаторы, как при создании обычной задачи.",
                            "Добавляем `TaskRow` в текущую Session без аргумента id. Вызываем `flush()`, "
                            "чтобы SQLite выполнил INSERT. Если собрать `Task` раньше, номер у строки "
                            "ещё будет None.",
                            "Преобразуем ORM-строку в `Task`. Внешняя область из первой карточки "
                            "подтверждает запись при выходе, прежде чем сервис отдаст эту задачу вызывающему.",
                        ],
                        "code": (
                            "# Добавьте к импортам app/DBase/repositories.py.\n"
                            "from app.validators import validate_priority, validate_title\n"
                            "\n"
                            "# Внутри SqlTaskRepository:\n"
                            "    def create(self, title: str, priority: int) -> Task:\n"
                            "        title = validate_title(title)\n"
                            "        priority = validate_priority(priority)\n"
                            "        row = TaskRow(\n"
                            "            title=title, priority=priority, is_done=False,\n"
                            "            tags=[], title_normalized=title.lower(),\n"
                            "        )\n"
                            "        self.session.add(row)\n"
                            "        self.session.flush()\n"
                            "        return task_from_row(row)"
                        ),
                    },
                },
                {
                    "title": "Заменяем поля задачи по её номеру",
                    "task": "Посмотрите, как в примере с продлением выдачи старый номер ведёт к нужной "
                            "строке, а при изменении срока пересчитываются часы. Добавьте "
                            "`replace(self, task)` в `SqlTaskRepository`. Сервис уже передаёт `Task` "
                            "с прежним номером и новыми значениями из PUT.",
                    "requirements": [
                        "Поиск идёт по `task.id`; при отсутствии строки метод возвращает None.",
                        "В найденной строке обновлены `title`, `priority`, `is_done` и "
                        "`title_normalized`. Номер и метки остаются прежними.",
                        "Метод возвращает обновлённую `Task` и оставляет завершение транзакции сервису.",
                    ],
                    "result": "PUT меняет поля той же задачи. Соседняя строка с таким же заголовком не затрагивается.",
                    "hint": "При продлении часы вычислялись из нового срока. Какое поле в Planner так же "
                            "зависит от нового заголовка?",
                    "answer": {
                        "explanation": "Меняем найденную по номеру ORM-строку. SQLAlchemy запомнит "
                                       "присваивания и запишет их при завершении транзакции.",
                        "steps": [
                            "Ищем строку по `task.id`. Заголовки могут совпадать у разных задач. Если "
                            "записи нет, возвращаем None. Сервис уже умеет обработать этот случай.",
                            "Переносим новый заголовок, приоритет и статус из объекта `Task`. При "
                            "переименовании «Купить ЧАЙ» в «Оплатить СВЕТ» поисковая копия должна стать "
                            "«оплатить свет». Старое «купить чай» могло бы остаться незаметным в ответе "
                            "PUT, потому что это внутреннее поле.",
                            "Оставляем id и метки найденной строки и возвращаем её как `Task`. "
                            "Когда сервис выйдет из области записи, изменения подтвердятся.",
                        ],
                        "code": (
                            "# Внутри SqlTaskRepository:\n"
                            "    def replace(self, task: Task) -> Task | None:\n"
                            "        row = self.session.get(TaskRow, task.id)\n"
                            "        if row is None:\n"
                            "            return None\n"
                            "\n"
                            "        row.title = task.title\n"
                            "        row.priority = task.priority\n"
                            "        row.is_done = task.is_done\n"
                            "        row.title_normalized = task.title.lower()\n"
                            "        return task_from_row(row)"
                        ),
                    },
                },
            ],
            "result": "В SQL-сборке Planner создаёт задачу с номером от SQLite и заменяет публичные "
                      "поля по этому же номеру.",
        }
    ],
    "71.1": [],
    72: [],
    "72.1": [],
    "72.2": [],
    73: [],
    "73.1": [],
    "73.2": [],
    74: [],
    "74.1": [],
    75: [],
    "75.1": [],
}
