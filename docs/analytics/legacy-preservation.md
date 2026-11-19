# Historical SQLite preservation smoke

Статус: **SOURCE PREPARED; live UNRUN**. Runner не запускает приложение или
Docker без явных `--run` и `LEGACY_PRESERVATION_RUN_GO=1`. Этот сценарий не
подтверждает UI, production или перенос данных. Его назначение — проверить
исторические HTTP-чтения на отдельной копии.

Источник — `services/core-api/.local/legacy/test.db`; byte baseline 425984 и
SHA-256 `95c70d6a338a8fd8222be9b1bdc9510146caa0cdb3d188fdbd7fdcad8e352964`
записаны в `snapshot.json` обоих архивов 14.11.2026. Manifest содержит размер и
hash, но не mtime. Runner сравнивает оба manifest и три файла до работы, затем
сравнивает размер, hash и фактический mtime каждого файла после неё; при WAL,
SHM или journal рядом останавливается. Он не открывает исходные файлы через
SQLite. Персональная копия и локальные следы принадлежат отдельному UUID в
`.local/analytics-legacy-smoke/`, остаются после успеха или сбоя и требуют
контролируемого хранения и последующей отдельной очистки владельцем. До копирования
runner отключает наследование Windows ACL в новом каталоге и проверяет полный
доступ только текущего пользователя и SYSTEM; ошибка ACL останавливает работу.

Frozen source: checkout `knowi-prod-release`, commit
`4107334d8328b8d8b791322d67951d7ffb87890e`; рабочие Core и backend paths должны
быть чистыми. В новый build context извлекаются только ограниченные tracked
requirements, content, Core source и Dockerfile из `git archive` этого commit.
SQLite, `.env`, ключи, ссылки и выход за context запрещены. Два `FROM` в копии
Dockerfile фиксируются проверенным локальным `python@sha256:<manifest>`; runner
получает manifest из `RepoDigests` и отдельно сверяет его с указанным оператором
image config ID. При отсутствии или неоднозначности manifest сборка не начинается.
[Dockerfile reference](https://docs.docker.com/reference/dockerfile/#from)
описывает ссылку `<image>@<digest>`; config ID и manifest digest различны.
По умолчанию сборка использует `--dependency-network none`; явный
`--dependency-network default` разрешает только build-контейнеру скачать
зависимости после проверки полного context. Runtime всегда использует
`network=none`. Runner создаёт отдельный Docker config только с проверенным
локальным npipe и пустыми auth/proxy, host credentials и proxy-переменные не
передаются; proxy build args принудительно пустые, индекс pip задан явно. `--pull=false`
сохраняется. Совместимый base должен уже существовать локально.

Frozen `requirements.txt` содержит одну нефинитную строку `a2wsgi>=1.10`.
Отдельный generated constraint фиксирует `a2wsgi==1.10.10`, не меняя исходник;
это выбранный кандидат, а не доказанная версия исторической установки. Остальные
строки frozen requirements закреплены через `==`. Пакет и версия доступны на
[PyPI](https://pypi.org/project/a2wsgi/1.10.10/). В operation records сохраняются
SHA-256 исходного и generated Dockerfile, requirements, constraint, полного
build context, итогового image и фактически установленных имён/версий пакетов.
Этот fingerprint не доказывает provenance скачанных wheel или воспроизводимую
побайтово сборку; образ и зависимости требуют live-проверки.

После отдельного GO укажите абсолютный путь локального Docker CLI, его SHA-256,
контекст с локальным Windows npipe и заранее проверенный локальный base image ID:

```powershell
$env:LEGACY_PRESERVATION_RUN_GO = '1'
.\.venv\Scripts\python.exe -B tools/test_legacy_preservation.py --run `
  --run-id '<НОВЫЙ UUIDv4>' --docker '<АБСОЛЮТНЫЙ DOCKER.EXE>' `
  --docker-sha256 '<SHA256 EXE>' --context '<ЛОКАЛЬНЫЙ КОНТЕКСТ>' `
  --base-image-id 'sha256:<ПРОВЕРЕННЫЙ IMAGE ID>' --dependency-network default
```

Команда выполняется из `services/analytics`. Сначала проверить ресурсное окно,
Docker/контекст/base и совместимость зависимостей; подготовка образа и запуск
не проводились. На каждом внешнем шаге создаётся exclusive pending record.
Build/create/start/wait пишут в защищённый каталог bounded stdout/stderr и
результат CLI: ненулевой код фиксируется как известный сбой команды с необходимой
сверкой внешнего состояния, таймаут — как неизвестный исход. Строки с credentials
подавляются; произвольный вывод runtime не сохраняется, чтобы исключить
пользовательские данные. Таймаут или ошибка не инициируют повтор или удаление
ресурсов. Сверить запись, фактические image/container ID и состояние до нового действия. Контейнер
создаётся с `network=none`, без опубликованных портов, UID/GID 10001,
read-only root, `cap-drop=ALL`, no-new-privileges, ограничениями памяти/CPU/PID.
До старта проверяется Docker inspect и bind только нового каталога копии.
Оригинальные файлы в контейнер не монтируются.

`legacy_sqlite` startup выполняет DDL и запись defaults **в копию**. Затем
создаются синтетические student/admin и переносятся строки одного имеющегося
исторического пользователя в synthetic student на копии. Логин проходит через
штатный HTTP; calendar/summary, admin overview/profile и learning catalog идут
по loopback внутри контейнера; доступ без сессии и от student к admin проверяется
отдельно. Calendar сопоставляется с данными копии; статус доступных исторических
уроков сравнивается между catalog и admin profile, admin total — с текущим числом
студентов копии. При отсутствии подходящего исторического примера
или несовместимости контента сценарий закрывается с FAIL, а не выдаёт PASS.
Вывод содержит только булевы результаты и статус; реальные ответы, имена,
email, cookies и содержимое SQLite не сохраняются в отчёте.

Офлайн-проверка без GO:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/deployment/test_legacy_preservation.py -q
```

Тесты используют синтетические файлы и mock HTTP; они не подтверждают реальную
сборку, mount Docker Desktop, работоспособность старого сервиса, данные или
историческую совместимость. Сохранённые исходные записи/образы/артефакты вне
каталога операции не удаляются и не модифицируются runner.

Попытка `183575ba-c63e-43f1-9133-37991af75d07` остановилась до создания
`owner.json`, копии SQLite и вызова Docker. Исходная база и обе архивные копии
сохранили исторический SHA256
`95c70d6a338a8fd8222be9b1bdc9510146caa0cdb3d188fdbd7fdcad8e352964`,
размер 425984 байта и исходные mtime; SQLite sidecar-файлы отсутствовали.
Созданный UUID-каталог был пуст и имел защищённый ACL только для пользователя
и SYSTEM. Причина остановки: Windows PowerShell 5.1 получил унаследованный путь
модулей PowerShell 7; автоматическая загрузка Security module завершилась
ошибкой из-за несовместимых TypeData. Пустой каталог удалён после повторной
проверки пути, ACL и отсутствия содержимого. Это не результат smoke.

Попытка `107c2712-4598-4a26-9e2c-622834061e3e` собрала образ и создала
контейнер, но завершилась `FAIL`. Отдельная диагностика
`767b3070-6452-4d16-8532-6bd79978d23d` на том же образе и сохранённой копии
показала фазу `seed_copy`: в исторической базе есть связанные calendar и
progress одного пользователя, но он admin. Прежний fixture ошибочно требовал
historical non-admin. Сценарий теперь выбирает профиль любой роли и копирует
его записи новому synthetic student; исходный аккаунт и его роль не меняются.
Если совместных исторических calendar и progress нет, сценарий выдаёт FAIL.
Оба диагностических результата не являются успешным smoke.

Для проведённого повтора использован `tools/legacy_preservation/reuse_image.py`: он
сверяет подтверждённый образ из попытки 107c и stopped container, заново
проверяет исходные файлы, создаёт защищённый UUID-каталог и новую копию SQLite.
Скорректированный runtime монтируется read-only, сборка не выполняется. Перед
create/start/wait сохраняются pending records; ошибки и неизвестные исходы
не повторяются автоматически. Контейнер и копия остаются до сверки и решения
об очистке. Историческая команда запуска была такой:

```powershell
$freeMiB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024
$diskMiB = (Get-PSDrive C).Free / 1MB
if ($freeMiB -lt 4096 -or $diskMiB -lt 8192) { throw 'Resource preflight failed' }
$runId = [guid]::NewGuid().ToString()
Write-Output "RUN_ID=$runId SUPERVISOR_PID=$PID"
$env:LEGACY_PRESERVATION_REUSE_GO = '1'
& '.\services\analytics\.venv\Scripts\python.exe' -B '.\services\analytics\tools\legacy_preservation\reuse_image.py' --run-id $runId
```

Запуск предполагает рабочий каталог workspace и проверку ресурсов в отдельном
окне без конкурирующей нагрузки. После очистки этого точного образа команда не
может быть повторена без его отдельной подготовки и нового разрешения. Нельзя
интерпретировать подготовку runner или синтетические тесты как результат
исторического HTTP smoke.

Повтор `6e7c1384-9ca8-407c-b52e-355b9db2fcd6` с новой защищённой копией и
подтверждённым образом 107c завершился `PASS`: штатный HTTP-вход synthetic
student/admin, отрицательные проверки доступа, calendar, summary, admin profile
и progress/catalog сравнивались с историческими данными копии. Контейнер
`9b9c7b7488122e3ddc3903c340d6d4530251eb1aed681d0418b3a5d58f90ec23` завершился
с кодом 0 без OOM; сеть none, опубликованных портов нет. До и после SHA256,
размер и mtime исходной SQLite и двух архивных копий совпали, sidecars не было.
Это локальная проверка на копии с synthetic аккаунтами и frozen build; она не
является проверкой production, UI или входа исторического пользователя.

После подтверждения результата три собственных остановленных контейнера, их
три UUID-каталога с приватными копиями и временный untagged образ удалены по
точным идентификаторам. Исходные файлы повторно прошли проверку SHA256, размера,
mtime и отсутствия sidecars. Сохранены 48 receipts и redacted diagnostics в
`.local/analytics-legacy-audit/916e3969fed649d7ba44de938c74a6dc`: SHA256
`manifest.json` — `e7e91f132837382d4fa93fe63baf8e87d3b1cc6076394936e512ae74a47049c5`,
SHA256 `cleanup-receipt.json` —
`9aa946ade50a9119ed18c9efa47637af0e201d97b817f9c3a79579f489b330c4`.
