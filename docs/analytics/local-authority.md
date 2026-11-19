# Локальная privacy authority

Статус: исходный код подготовлен для отдельного режима local. Это не запуск и
не подтверждение готовности production. Синтетический CLI и его fixture-режим
сохраняют прежнее поведение.

Локальная authority использует независимый SQLite journal и прежний подписанный
протокол HWM, непрерывного применения и purge. Файлы принадлежат только новому
каталогу `services/analytics/.local/local-live/<INSTANCE_UUID>/`; UUID в имени
канонический и совпадает с `instance_id` в owner. Родитель `local-live` и сам
каталог заранее создаются владельцем с приватными правами. На Windows проверка
допускает разрешающие ACL только текущему SID и SYSTEM, требует FullControl
текущего SID и владельца current SID или SYSTEM; на POSIX каталог и файлы должны
принадлежать текущему пользователю без прав группы и остальных. Символьные
ссылки, junction и файлы с несколькими hard links отклоняются. Эти проверки не
шифруют журнал и не заменяют политику защиты компьютера.

`owner.json` — приватный файл с точными полями:

```json
{
  "version": 1,
  "mode": "local",
  "do_not_drop": true,
  "instance_id": "<INSTANCE_UUID>",
  "core_instance_id": "<DISTINCT_CORE_UUID>",
  "dataset_run_id": "<SHARED_PROJECTION_RUN_UUID>",
  "authority_id": "local-authority:<INSTANCE_UUID>"
}
```

`dataset_run_id` должен совпадать с внешним UUID обоих projection workers и
читателя. Проверка authority не устанавливает это совпадение сама. Core UUID
отдельный, все три UUID непустые. Оператор создаёт отдельные ключи подписания
authority, P1, G1 и команды Core; эти файлы и reader secret не выводятся в лог.

`authority-server.json` хранится в том же приватном каталоге:

```json
{
  "mode": "local",
  "fixture_mode": false,
  "bind_host": "127.0.0.1",
  "port": 18046,
  "issuer": "local-authority:<INSTANCE_UUID>",
  "kid": "a1",
  "signing_key_file": "authority.private.pem",
  "producer_issuer": "local-producer:<INSTANCE_UUID>",
  "producer_public_keys": {"p1": "<P1_PUBLIC_PEM>"},
  "allowed_producers": ["core-local"],
  "producer_command_public_keys": {"core-local": "<COMMAND_PUBLIC_PEM>"},
  "reader_secret_file": "authority.reader"
}
```

Приватный Ed25519 `authority.private.pem` и `authority.reader` находятся в том
же каталоге. Reader secret — canonical base64url без `=` от 32 случайных байт.
P1 и command public PEM вставляются в server config; сервер проверяет, что ключи
authority, P1 и command различаются. G1 не передаётся серверу и должен иметь
отдельный ключ. Настройки клиента содержат тот же `authority_id` и `issuer`,
URL `http://127.0.0.1:18046`, `public_keys: {"a1": <AUTHORITY_PUBLIC_PEM>}`,
`reader_secret`, `fixture_mode: false`, `runtime_mode: "local"`. Local клиент
отклоняет DNS-имена, внешние адреса, пути, query и другие схемы. Его secret JSON
также хранится только в указанном приватном каталоге.

После подготовки владельцем owner/config и ключей отдельная инициализация
создаёт `authority.sqlite` только если его и SQLite sidecars ещё нет:

```powershell
python -B -m pymentor_analytics.adapters.privacy_authority.local_server init --instance-id <INSTANCE_UUID>
python -B -m pymentor_analytics.adapters.privacy_authority.local_server serve --instance-id <INSTANCE_UUID>
```

`serve` принимает только существующий валидный journal и слушает только
`127.0.0.1:18046`; restart не вызывает повторный init. Ошибка, незавершённая
инициализация, существующий журнал или неизвестный исход требуют сохранения
файлов и отдельной сверки. Автоматических удаления, переноса, повторного init
или отката journal нет. Настоящий пользовательский профиль и доставка ещё
требуют проверки полного процесса и не подтверждаются unit-тестами.
