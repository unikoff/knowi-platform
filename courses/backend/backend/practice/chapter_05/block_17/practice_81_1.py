"""Project practice for the stable restoration lesson."""

RESTORE_CODE = '''from pathlib import Path
import sqlite3


def restore(snapshot, destination):
    snapshot = Path(snapshot).resolve()
    destination = Path(destination).resolve()
    if snapshot == destination:
        raise ValueError("Источник и назначение совпали")
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    source = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
    try:
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return destination
'''

VERIFY_CODE = '''FIELDS = ("id", "title", "priority", "is_done", "tags", "title_normalized")


def inventory(path):
    path = Path(path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchall(),
            "objects": connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall(),
            "columns": connection.execute("PRAGMA table_info(tasks)").fetchall(),
            "indexes": connection.execute("PRAGMA index_list(tasks)").fetchall(),
            "rows": connection.execute(
                "SELECT id, title, priority, is_done, tags, title_normalized "
                "FROM tasks ORDER BY id"
            ).fetchall(),
        }
    finally:
        connection.close()


def check(candidate, expected):
    actual = inventory(candidate)
    if actual["integrity"] != [("ok",)]:
        raise ValueError("Ошибка целостности SQLite")
    for part in ("objects", "columns", "indexes"):
        if actual[part] != expected[part]:
            raise ValueError(f"Изменилась структура: {part}")
    actual_by_id = {}
    expected_by_id = {}
    for row in actual["rows"]:
        actual_by_id[row[0]] = row
    for row in expected["rows"]:
        expected_by_id[row[0]] = row
    for task_id in expected_by_id:
        if task_id not in actual_by_id:
            raise ValueError(f"Потерян ID {task_id}")
        for position in range(len(FIELDS)):
            if actual_by_id[task_id][position] != expected_by_id[task_id][position]:
                raise ValueError(f"ID {task_id}: изменено поле {FIELDS[position]}")
    for task_id in actual_by_id:
        if task_id not in expected_by_id:
            raise ValueError(f"Лишний ID {task_id}")
    return actual
'''

NEGATIVE_CODE = '''# Этот код дополняет app/DBase/restore_db.py.
# Вызов добавляется только под if __name__ == "__main__".
def negative_probe(backup, bad_snapshot, rejected, expected, removed_id):
    restore(backup, bad_snapshot)
    connection = sqlite3.connect(bad_snapshot)
    try:
        connection.execute("DELETE FROM tasks WHERE id = ?", (removed_id,))
        connection.commit()
    finally:
        connection.close()
    restore(bad_snapshot, rejected)
    try:
        check(rejected, expected)
    except ValueError as error:
        print("Неполный снимок: ОТКАЗ.", error)
'''

VERIFY_RUN_CODE = '''# После определений в app/DBase/restore_db.py:
if __name__ == "__main__":
    source = Path(input("Неизменённый исходник: ")).resolve()
    restored = Path(input("RESTORED: ")).resolve()
    expected = inventory(source)
    actual = check(restored, expected)
    print("Принятая копия:", actual["rows"])
    backup = Path(input("Успешный BACKUP: ")).resolve()
    bad = Path(input("Новый BAD_SNAPSHOT: ")).resolve()
    rejected = Path(input("Новый REJECTED: ")).resolve()
    removed_id = int(input("Удаляемый существующий ID из описи: "))
    negative_probe(backup, bad, rejected, expected, removed_id)

# PowerShell из project/ после сверки отпечатка SOURCE:
# .\\.venv\\Scripts\\python.exe -m app.DBase.restore_db
'''

MANUAL_PRACTICE = {
    "81.1": [
        {
            "title": "Проверенная рабочая копия Planner",
            "task": (
                "Работаем в прежнем project/ с его .venv. На входе нужен успешный резерв "
                "синтетической SQLite с двумя задачами, исходная опись схемы и всех полей "
                "из темы согласованного резервирования. Исходник и хороший резерв сохраняем. "
                "Если резервирование завершилось частично или опись не сохранена, сначала "
                "возвращаемся к подготовке резерва, а не создаём пустую базу вместо входа."
            ),
            "tasks": [
                {
                    "title": "Адреса и независимый эталон",
                    "task": (
                        "Покажем исходную учебную базу, именно успешно полученный резерв и "
                        "сохранённую до резервирования опись. Прочитаем одну исходную запись "
                        "и убедимся, что понимаем её id, title, priority, is_done, tags и "
                        "title_normalized. В observations.md продолжим запись адресов и результатов. "
                        "Выберем новый отсутствующий файл для рабочей копии, другой отсутствующий "
                        "адрес для будущей baseline и ещё два свободных имени для неполного снимка "
                        "и отвергнутого кандидата. Имена из книжного примера не используем. "
                        "Остановим писателей исходной базы и зафиксируем отпечатки исходника и "
                        "резерва до операций. Эта карточка ещё не создаёт ни одну новую SQLite."
                    ),
                    "requirements": [
                        "В observations.md записаны абсолютные адреса SOURCE, BACKUP, RESTORED, BASELINE_NEW, BAD_SNAPSHOT и REJECTED; все адреса различаются.",
                        "SOURCE и BACKUP существуют, снимок открывается для чтения; исходная опись содержит схему, ограничения и обе полные строки из шести Task-полей.",
                        "RESTORED, BASELINE_NEW, BAD_SNAPSHOT и REJECTED отсутствуют; даже пустой существующий файл считается занятым.",
                        "Писатели SOURCE остановлены. Отпечатки SHA-256 SOURCE и BACKUP сохранены до дальнейших операций, вместе с адресами и исходной описью.",
                    ],
                    "result": "В observations.md видны независимые роли адресов, исходная опись и два контрольных отпечатка; новые цели ещё не созданы.",
                    "hint": "Вернёмся к схеме трёх файлов и команде отпечатка. Сначала сопоставим фактический абсолютный адрес снимка с записью о его успешном создании.",
                    "answer": {
                        "explanation": "Эталон появился до резервирования. Адрес будущей baseline остаётся свободным, а существующий резерв не становится рабочим файлом приложения.",
                        "steps": [
                            "Откроем прежнюю observations.md, найдём запись об успешном резерве и обе исходные Task-строки. Сверим шесть полей и фактическую структуру, включая CHECK и индексы.",
                            "Запишем шесть абсолютных адресов и их роли. Для четырёх новых целей проверим отсутствие файлов; при занятом имени выберем другое, не удаляя старый файл.",
                            "После завершения API/CLI и соединений выполним приведённую команду отдельно для SOURCE и BACKUP и сохраним оба результата в observations.md.",
                            "Если исходные строки или успешный снимок не подтверждены, вернёмся к созданию резерва. Не заменяем вход новым пустым файлом.",
                        ],
                        "code": r'''.\.venv\Scripts\python.exe -c "from pathlib import Path; import hashlib; p=Path(input('Путь файла: ')).resolve(); print(p); print(hashlib.sha256(p.read_bytes()).hexdigest())"''',
                        "checks": [
                            "Команда выполнена дважды на существующих SOURCE и BACKUP; отпечатки связаны с правильными адресами.",
                            "BASELINE_NEW остаётся отсутствующим. Непустая или частичная цель не очищена ради продолжения.",
                        ],
                    },
                },
                {
                    "title": "Собственная операция восстановления",
                    "task": (
                        "В app/DBase/restore_db.py либо в своём существующем backup-модуле "
                        "самостоятельно напишем операцию восстановления из снимка в новый путь. "
                        "Названия функции и внутреннее устройство можно выбрать, но направление, "
                        "отказ на неверных адресах и закрытие ресурсов должны быть видны. "
                        "Применим её к BACKUP и RESTORED из observations.md. Пока считаем результат "
                        "кандидатом, а не готовым Planner. Затем попробуем восстановить на уже "
                        "занятый RESTORED, на совпадающий с источником адрес и из отсутствующего "
                        "источника. Сравним контрольные отпечатки и зафиксируем отказы."
                    ),
                    "requirements": [
                        "В существующем проекте есть написанная операция с явным источником-снимком и новым назначением; API/CLI и TaskRow не переписаны.",
                        "Снимок открывается только для чтения; новое назначение создаётся исключительно, backup направлен от снимка к новой цели.",
                        "Одинаковые адреса, отсутствующий источник и любое существующее назначение получают отказ до изменения защищаемых файлов; ложный источник не создаётся.",
                        "Открытые соединения закрываются при успехе и исключении. Частичная новая цель не считается успешной и не используется для повторной записи.",
                        "RESTORED открывается новым соединением. SOURCE/BACKUP сохранили исходные отпечатки; повтор на RESTORED не изменил его отпечаток.",
                    ],
                    "result": "Существует отдельный RESTORED-кандидат. Неверные адреса получили наблюдаемые отказы, исходник, резерв и занятое назначение сохранены.",
                    "hint": "В разборе двух соединений сначала назовём, у какого объекта вызывается backup и какое соединение передано аргументом. Затем отдельно проверим создание назначения через x и закрытие в finally.",
                    "answer": {
                        "explanation": "Вариант ниже добавляется в app/DBase/restore_db.py. Он реализует только перенос и guard; пригодность данных ещё предстоит установить следующей карточкой. Готовая книжная программа не импортируется в рабочий Planner.",
                        "steps": [
                            "Приведём оба пути к абсолютному виду и откажем при совпадении или отсутствии источника. Это предотвращает подмену снимка пустой базой.",
                            "Откроем снимок read-only и зарезервируем новое назначение через xb. Ошибка существующего имени не должна приводить к backup поверх старого файла.",
                            "Выполним source.backup(target) и закроем каждое открытое соединение в finally. Возвращаем адрес кандидата, не общий статус пригодности.",
                            "Из project/ вызовем свою функцию с фактическими BACKUP/RESTORED. Для варианта с именем restore подходит команда: Python -c с импортом restore и двумя Path(input()). Запускаем тем же .venv, что и Planner.",
                            "Повторим неверные случаи и команду отпечатка из первой карточки. Сопоставим SOURCE/BACKUP с прежней записью, RESTORED до и после отказа на занятом имени.",
                        ],
                        "code": RESTORE_CODE + r'''
# Из PowerShell в project/ после сохранения модуля:
# .\.venv\Scripts\python.exe -c "from pathlib import Path; from app.DBase.restore_db import restore; print(restore(Path(input('Снимок: ')), Path(input('Новая цель: '))))"
''',
                        "checks": [
                            "Существующий BACKUP и свободный RESTORED дают новый файл; открытие источника не создаёт отсутствующий снимок.",
                            "Повтор на RESTORED даёт FileExistsError и сохраняет его отпечаток; одинаковые адреса дают отказ до копирования.",
                            "Факт открывания кандидата не записан как доказательство сохранности всех задач.",
                        ],
                    },
                },
                {
                    "title": "Все значения и неполный снимок",
                    "task": (
                        "Добавим к своей процедуре чтение фактической схемы и всех Task-полей, "
                        "сравним RESTORED с исходной описью. Проверим integrity_check отдельно, "
                        "затем колонки, PK, nullable, SQL-default, CHECK и индексы, множество ID "
                        "и каждое значение title/priority/is_done/tags/title_normalized. "
                        "Сохраним результаты групп в observations.md. Для отрицательного опыта "
                        "получим отдельную копию BACKUP в BAD_SNAPSHOT. Только в ней удалим одну "
                        "выбранную исходную задачу по её записанному ID с commit. Восстановим этот "
                        "образец в новый REJECTED и применим те же проверки. Укажем точную причину "
                        "отказа, даже если SQLite вернула ok. RESTORED и хороший BACKUP не меняем."
                    ),
                    "requirements": [
                        "Проверка заново открывает кандидата после завершения restore. Только точный результат integrity_check [(\"ok\",)] считается исправной внутренней структурой.",
                        "Схема и ограничения сопоставлены с исходной описью; Python-default не выдан за SQL-default. Индексы и их колонки соответствуют исходному составу.",
                        "Совпадают множество ID и все шесть Task-полей по ID. Публичный API-вывод или одно число строк не заменяет эту сверку.",
                        "BAD_SNAPSHOT получен отдельной новой копией успешного резерва; только в нём удалён выбранный ID, запись завершена commit, соединение закрыто.",
                        "REJECTED создан на другом новом пути. Проверка отвергла его с потерянным ID; ни один плохой кандидат не получил общий статус успеха и не подключён к runtime.",
                        "В observations.md раздельно записаны целостность, схема, ID/значения и причина отказа плохого образца. Исходник и хороший резерв сохранились.",
                    ],
                    "result": "RESTORED прошёл сверку структуры и всех значений. Открываемый REJECTED отвергнут с конкретным потерянным ID, без ручного восстановления строки.",
                    "hint": "Сначала проверим, откуда взят эталон: он должен соответствовать описи до backup. Затем вернёмся к образцам B/C в книжном интерактиве и сравним ID отдельно от значений.",
                    "answer": {
                        "explanation": "Вариант чтения и сравнения ниже дополняет модуль восстановления. Исходную inventory читаем до операции либо только после подтверждения прежнего отпечатка SOURCE и совпадения с сохранённой описью. Эталон из проверяемого снимка не строим. Подробные части SQL и свойств разобраны в теории; здесь переносим их на шесть Task-полей.",
                        "steps": [
                            "Сверим отпечаток SOURCE с первой карточкой и её исходные строки с observations.md. Получим expected через inventory этого неизменённого источника. Результат не берётся из RESTORED или BACKUP.",
                            "Добавим inventory и check ниже restore. inventory читает структуру и упорядоченные полные строки, check отделяет целостность, свойства схемы, множество ID и поля.",
                            "Вызовем check(RESTORED, expected). При успехе выведем строки и отдельно запишем результаты групп. Если появились расхождения, найдём причину в резерве/описи и повторим restore на новом имени, не лечим строку вручную.",
                            "Добавим negative_probe и блок явного запуска ниже. Команда в конце кода запускает модуль и последовательно запрашивает фактические пути и выбранный ID из исходной описи. DELETE выполняется только в BAD_SNAPSHOT и завершается commit.",
                            "Сопоставим вывод отказа с удалённым ID. Даже при ok и прежней схеме эта копия не проходит check. Изменение одного поля при прежних ID тоже выявляется сравнением полей.",
                        ],
                        "code": VERIFY_CODE + "\n\n" + NEGATIVE_CODE + "\n\n" + VERIFY_RUN_CODE,
                        "checks": [
                            "В принятой копии совпали ID, title, priority, is_done, tags и title_normalized, а также исходные свойства схемы.",
                            "После удаления одной строки из BAD_SNAPSHOT проверка REJECTED сообщает потерянный ID и не возвращает успех.",
                            "На изменённом случае книжного интерактива объяснено, почему совпадение числа строк и ID не оправдывает изменённое поле.",
                        ],
                    },
                },
                {
                    "title": "Обычный Planner читает принятую копию",
                    "task": (
                        "Подключим прежнюю сборку Planner только к RESTORED, который прошёл все "
                        "проверки. Сохраним прежнее окружение терминала, зададим явный абсолютный "
                        "DATABASE_URL и выведем эффективный адрес load_settings. Последовательно "
                        "прочитаем задачи через CLI list/exit и через API GET /tasks без фильтра "
                        "с limit 50. После остановки API заново запустим CLI и повторим чтение. "
                        "Новых задач не добавляем, done/delete и init_db не выполняем. "
                        "Затем вернём окружение терминала, остановим процессы и завершим observations.md: "
                        "адрес резерва, опись, совместимый код, восстановленная копия, её URL и другой "
                        "всё ещё отсутствующий BASELINE_NEW. REJECTED не включаем в рабочую передачу."
                    ),
                    "requirements": [
                        "DATABASE_URL задан до создания engine/Session и указывает на принятый RESTORED; эффективное значение проверено, постоянный .env не изменён.",
                        "Прежние CLI и API читают ожидаемые ID/title/priority/is_done. Внутренние tags/title_normalized подтверждены предыдущей SQL-сверкой, а не публичным ответом.",
                        "Новый CLI-процесс после остановки API повторяет чтение того же набора; /health не используется как доказательство чтения задач.",
                        "SOURCE/BACKUP сохранили прежние отпечатки и опись, RESTORED не изменён читательскими проверками; окружение возвращено, процессы остановлены.",
                        "Восстановимая точка содержит BACKUP, исходную опись и совместимый код. Заполненный RESTORED передан отдельно от отсутствующего BASELINE_NEW; негодные файлы изолированы.",
                    ],
                    "result": "Два прежних интерфейса и повторный CLI видят ожидаемые задачи по URL RESTORED. Есть воспроизводимая точка возврата и отдельный свободный адрес новой baseline.",
                    "hint": "Вернёмся к моменту чтения DATABASE_URL в разделе о новом процессе. Сначала проверим эффективный адрес, а не создаём таблицы на случайно выбранном файле.",
                    "answer": {
                        "explanation": "Новое соединение подтверждает чтение сохранённого файла, новый процесс дополнительно подтверждает повторную загрузку настройки. Последовательный запуск позволяет выполнить опыт в одном терминале с одним URL.",
                        "steps": [
                            "Запомним прежнее значение окружения и зададим абсолютный URL RESTORED. Выведем load_settings().database_url тем же Python; сверим фактический путь с observations.md.",
                            "Запустим CLI, введём list, сравним ID и публичные поля, затем exit. Команды меню не вводим в PowerShell.",
                            "Запустим API с тем же окружением. В локальном /docs выберем GET /tasks, Try it out, limit 50 без фильтров, Execute. Проверим обе исходные задачи.",
                            "Остановим API Ctrl+C, снова запустим CLI и повторим list/exit. Не выполняем add/done/delete/init_db ради подтверждения.",
                            "Повторим команду отпечатков и сравним с первой карточкой. Вернём окружение, завершим запись точки возврата и убедимся, что BASELINE_NEW отсутствует.",
                        ],
                        "code": r'''# PowerShell из project/
$HadDatabaseUrl = Test-Path Env:DATABASE_URL
$PreviousDatabaseUrl = $env:DATABASE_URL
$env:DATABASE_URL = Read-Host "Абсолютный SQLite URL принятой копии"
.\.venv\Scripts\python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"
.\.venv\Scripts\python.exe -m app.main
# В меню: list, затем exit
.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
# В локальном /docs: GET /tasks, limit 50. После ответа: Ctrl+C
.\.venv\Scripts\python.exe -m app.main
# В новом меню: list, затем exit
if ($HadDatabaseUrl) {
    $env:DATABASE_URL = $PreviousDatabaseUrl
} else {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}''',
                        "checks": [
                            "Эффективный URL совпал с RESTORED, оба интерфейса и новый CLI показали одну публичную проекцию исходных задач.",
                            "После завершения SOURCE/BACKUP сохранили отпечатки, RESTORED остался заполненным, BASELINE_NEW не создан.",
                            "Неполный образец не указан в инструкции как рабочая копия; совместимость исходного резерва привязана к прежнему коду.",
                        ],
                    },
                },
            ],
            "result": "Есть проверенная заполненная копия, различающий отказ для неполного снимка, подтверждённое чтение прежним Planner и сохранённая точка возврата с отдельным свободным адресом baseline.",
        }
    ]
}
CODE_TASKS = {}
