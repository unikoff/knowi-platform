# knowi01: backup и log retention

Специализированные host scripts для текущей PostgreSQL 16 knowi_core в K3s.
Они не меняют release приложения. Фактический запуск и rollback описаны в
[операционном протоколе](../../../docs/stage-01-k3s/operations/knowi-backup-rotation-20261113.md).

`backup.py`, `receiver.py`, `logs.py`, `common.py` установлены в
`/usr/local/lib/knowi-backup`; unit sources находятся в `systemd/`.
Server config `/etc/knowi-backup/config.json` содержит проверенные host/node,
Deployment UID, system_identifier, database OID и адрес Core DB. После миграции
эти anchors нужно проверить заново, а не отключать проверки. Получатель шифрования
`/etc/knowi-backup/recipient.pem` — только публичный сертификат.

Backup: Mon/Thu 03:00 Europe/Moscow, isolated pg_restore, CMS AES-256-GCM/RSA-OAEP,
последние четыре локально проверенные точки. Во время создания новой требуется
место для пятой; после успешного restore-check, шифрования и публикации старые
точки удаляются независимо от offsite ack и доступности компьютера. Если новая
копия не прошла проверку, предыдущие четыре сохраняются. Подтверждение выгрузки
только обновляет статус внешней копии. Независимый deletion ledger не очищается автоматически.
Экспорт журнала при backup не доказывает полноту после потери узла: обслуживание
восстановленной БД остаётся закрытым до сверки актуального журнала удалений.

`install_client.ps1 -SourcePython <python.exe>` создаёт закрытый каталог клиента,
отдельные encryption/SSH keys и Python runtime с Paramiko 4.0.0. Существующие
ключи сохраняются. Host key должен быть подтверждён через существующий доверенный
SSH-доступ. Private keys с компьютера на VPS не передаются. Pull SSH key на VPS
ограничен `restrict,command="/usr/bin/python3 /usr/local/lib/knowi-backup/receiver.py"`.

`register_task.ps1` копирует клиентские `pull.py`/`common.py` в профиль и создаёт
Windows task `Knowi encrypted backup pull`: Mon/Thu 12:00 Moscow, Interactive,
без startup/logon trigger, wake и catch-up. Требуются включённый компьютер,
вошедшая сессия пользователя и сеть. Она использует `pythonw.exe`, без окна.
Код выхода и `state/last-result.json` показывают фактический результат.

Log retention: kubelet 10 MiB × 5 (существующая настройка), возрастная проверка
каждые 10 минут, сжатые закрытые архивы до 14 дней, общий лимит 100 MiB на
namespace/container name. Активные файлы не обрезаются: rename в скрытый staging
рядом с исходником, CRI ReopenContainerLog, проверка writable FD, gzip с checksum,
только затем удаление закрытого staging. Это работает с узкими ReadWritePaths
systemd. Checkpoint позволяет повторить прерванную операцию. Host journald имеет
отдельный более строгий общий лимит 100 MiB / 14 дней и файл до 10 MiB.

Focused checks (не обращаются к рабочей БД):

```powershell
python -m unittest discover -s infra/operations/knowi_backup -p test_policy.py -v
```

На сервере нужны Python 3.12+, OpenSSL 3, `python3-grpcio`, K3s с CRI v1 и
доступ к PostgreSQL admin socket внутри проверенного pod. На компьютере нужны
OpenSSL (Git for Windows), отдельный Python runtime и приватные ACL профиля.
