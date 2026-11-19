# CORP-107: синтетическая нагрузка в локальном K3s

**Статус: подготовлено в исходном коде; живой прогон UNRUN.** Офлайн-тесты с подставными объектами не подтверждают работу K3s, PVC, сети, журналов или производительность. Источник профиля — [profile-v1.json](../../services/analytics/bench/profile-v1.json), общая HTTP-нагрузка и oracle — [benchmark_activity.py](../../services/analytics/tools/benchmark_activity.py). Runner: [benchmark_k3s.py](../../services/analytics/tools/benchmark_k3s.py).

В последнем зафиксированном checkpoint v2 server `96cc40b0…` защитно остановлен операцией `d11af186-fed2-4947-b71d-6fc7d8d9a703`: exit 137, `OOMKilled=false`, завершение не было graceful. Это историческое evidence, а не гарантия текущего состояния. Неуспешный кейс `b551ed25-a61a-4b3a-a77a-4dadf1dcb11a` и его active marker, отдельная БД, namespace, PVC/PV и объект binder сохранены; очистка binder не подтверждена. Его результат остаётся `failed/UNRUN`, повтор не запускался.

Для следующего read-only restart admission обычный guard по умолчанию по-прежнему допускает только exit 143. Узкий opt-in `protective_stop_receipt=run_k3s.PROTECTIVE_STOP_CONFIRMED` допускает только конкретную подтверждённую остановку `d11af186…`: проверяются связанные intent/confirmed, точные server ID и `FinishedAt`, exit 137, `OOMKilled=false`, сохранённые volumes и прочие прежние identity/network/cap guards. При изменении lifecycle, отсутствии или несовпадении записей — отказ; данный opt-in не разрешает start и не изменяет budget/floors.

Без аргументов runner выводит в stdout план: код планирования не читает профиль, окружение или кластер и не создаёт файлов результатов. План содержит 10/15/25/50 пользователей, пять наборов реплик C/P/Q: 1/1/1, 2/1/1, 1/2/1, 1/1/2, 2/2/2; по три независимых повтора. Каждый из 60 прогонов — ровно 300 секунд прогрева и 900 секунд измерения, как минимум 72 000 секунд в сумме без развёртывания и проверки.

```powershell
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/benchmark_k3s.py
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/benchmark_k3s.py --a011
services/analytics/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider services/analytics/tests/benchmark -q
```

Один `--run` выполняет **один** кейс в новом namespace и требует отдельный GO координатора на текущий ресурсный интервал. До GO нельзя использовать команду для запуска контейнеров, K3s, PostgreSQL или нагрузки. Подготовленный CLI принимает следующие обязательные параметры; значения здесь намеренно не подставлены, их нужно получить и сверить непосредственно перед допуском:

```text
--run --run-id <uuid-v4> --namespace knowi-analytics-bench-<uuid-hex>
--context k3d-knowi-analytics-01a1df75-v2 --cluster-uid <kube-system-uid>
--server-id <docker-container-id> --k3s-image-id sha256:<digest>
--retained-authority-image-id <sha256:digest-or-none>
[--retained-authority-stopped-id <full-stopped-container-id>]
--storage-class-uid <local-path-uid>
--kubectl <absolute-path> --kubectl-sha256 <sha256-hex>
--helm <absolute-path> --helm-sha256 <sha256-hex>
--image-ref docker.io/library/knowi-analytics@sha256:<manifest-digest>
--image-config-id sha256:<application-config-digest>
--authority-ref docker.io/library/knowi-analytics@sha256:<manifest-digest>
--authority-config-id sha256:<authority-config-digest>
--host-image-id sha256:<immutable-docker-id>
--host-authority-image-id sha256:<immutable-docker-id>
--archive-max-mib <128..8192>
--stage <10|15|25|50> --replicas <C,P,Q> --repeat <1|2|3>
--resource-window <approved-window-id>
--output services/analytics/.local/benchmark-results/<new-name>.json
```

Без `--a011` целевая миграция БД — историческая `a010`; флаг явно выбирает `a011` и включает day worker Processor и day Query с одним UUID набора данных. Для обоих режимов обязательны образ со всей историей до `a011`, новый Privacy-код и проверка полного packaged history и отдельного префикса целевой миграции. `--a011` не меняет состав профиля, SLO, интервалы, replica sets или floors. После включения Privacy в режиме `--a011` сначала запускается только Processor; runner ждёт реальные завершённые a010 и day backfill и отсутствие незавершённых или устаревших day rows, прежде чем включить Query и начать прогрев. Timeout оставляет ресурсы для reconciliation.

Профиль не задаёт историческое заполнение: каждый кейс использует свежую БД, ноль seed-событий из истории и 1200 секунд реальной нагрузки. Отдельная проверка Query на плотной 31-дневной истории этим не выполняется и записывается как `UNRUN`; результат 20-минутного кейса не подтверждает этот history gate.

До старта нагрузки результат фиксирует ожидаемую cardinality `stream_plan`: для 10/15/25/50 назначенных субъектов соответственно 6/9/15/30 активных, 2/2/3/6 с перекрытием и 8/11/18/36 потоков. В каждом кейсе два курса, по одному synthetic lesson на курс, две секции (`theory`, `practice`), один opening на поток и затем по 20 warmup и 60 измерительных сегментов на поток с шагом 15 секунд. Это план синтетических запросов к Collector и Query, а не продуктовая телеметрия; фактические ACK, запросы и результаты остаются отдельными наблюдениями. После миграции runner отдельно подтверждает отсутствие фактов до нагрузки. Отдельный query-only dense fixture не подменяет полный HTTP/profile gate и не меняет общий статус `UNRUN`.

Только после отдельных GO на ресурсное окно и на запуск ограниченного image source probe выставить `ANALYTICS_BENCHMARK_GO=1`, `ANALYTICS_BENCHMARK_IMAGE_PROBE_GO=1` и положительные `ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB` и `ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB`, назначенные координатором для этого окна. Если Web намеренно приостановлен, есть `--web-temporarily-paused`; guard отдельно подтверждает отсутствие его listener. Runner проверяет private kubeconfig, context и API, ID server container и `kube-system`, hash kubectl/Helm, StorageClass UID и общий ресурсный бюджет.

Если в обычной локальной инфраструктуре осталась остановленная authority со своим journal, `--retained-authority-stopped-id` допускается только вместе с её точным `--retained-authority-image-id`. Guard повторно подтверждает единственный full container ID, stopped state без restart policy, image, labels, owner и journal volume, ограничения и сети и резервирует все 128 MiB. Дополнительная authority с ожидаемым префиксом имени или любой работающий Docker-контейнер того же образа блокирует этот opt-in. При отсутствии флага прежний контракт не меняется: указанная retained authority должна работать, а неизвестная authority отклоняется. Отдельный pre-start `require_stopped_node_restart_budget` принимает пару keyword-аргументов `expected_stopped_authority_id` и `expected_stopped_authority_image_id`; полный резерв сохраняется и там. Это не меняет ограничения PostgreSQL по swap/pressure/memory events и не запускает authority.

До запуска Pod runner на точном node проверяет существование каждой immutable manifest-ссылки и SHA-256 её содержимого и config, а также `linux/amd64` и ожидаемый entrypoint. В OCI index напрямую ссылаться нельзя: manifest digest и config digest — разные идентификаторы, Docker `.Id` может обозначать index и не используется как config digest. Обе digest-ссылки повторно сверяются перед созданием binder, init Job, authority и Helm workloads. После такой независимой аттестации guard принимает прежний CRI `imageID` с config digest либо наблюдённую форму с точной canonical manifest-ссылкой в `imageID` и ожидаемым config digest в `image`; requested Pod image при этом должен совпадать с закреплённой manifest-ссылкой.

Для host образов указываются отдельно их immutable Docker ID; `RepoDigests` и registry не требуются. Runner выполняет bounded `docker image save --platform linux/amd64 <host-ID>` в новый приватный архив, проверяя размер и disk/memory floors. Из tar читаются только `manifest.json` и указанный им небольшой config: его SHA и `rootfs.diff_ids` должны совпасть с проверенным config node. Пути, ссылки и повторные entries проверяются; слои не извлекаются и их содержимое этим шагом не проверяется. Затем отдельный ограниченный source probe запускает доверенный собственный образ по тому же host ID и той же платформе с `--network=none`, read-only root, non-root UID, без mounts и с CPU/memory/PID limits; сравниваются все файлы приложения и миграций, линейная история миграций, requirements и Alembic config с текущим source. Это проверка содержимого доверенного собственного образа, а не защита от поддельного вывода враждебного Python внутри образа. Base image и установленные зависимости этим сравнением не проверяются. Node digest gate не заменяется host-проверкой. Runner не импортирует и не публикует образы автоматически.

Архивы остаются в приватном owner directory вместе с подтверждёнными записями; при несовпадении, timeout или превышении лимита сохраняются также partial archive и pending marker для reconciliation. Лимит `--archive-max-mib` задаётся на **каждый** из двух разных host образов, а при 60 кейсах сохраняемые архивы занимают дополнительное место: disk floor нужно назначать с учётом этого роста. Если оба роли используют один host ID и один config, проверка и архив повторно используются внутри этого кейса.

Для кейса создаются отдельная UUID-БД на synthetic Analytics PostgreSQL, roles/sentinel, выбранная миграция и отдельный namespace. Для authority используются свои ключи, Secret и PVC `local-path`: runner допускает только `WaitForFirstConsumer`, проверяет отсутствие каталога и symlink для нового PVC UID на целевом node, затем read-only binder позволяет проверить bound PVC/PV UID, provisioner, точный путь под `/var/lib/rancher/k3s/storage` и reclaim policy. Для PV с `spec.local` дополнительно требуются точный selected-node и ограниченная только им hostname nodeAffinity; одновременные `local` и `hostPath` отклоняются. Только после этих проверок одноразовый Job вызывает инициализацию журнала. Иная конфигурация storage требует отдельной адаптации и не допускается автоматически. Restart authority использует тот же PVC. `Delete` как reclaim policy фиксируется, но не даёт разрешения удалять PVC или namespace. Trust, catalog и role-specific Secrets передаются API через stdin; credentials не размещаются в arguments или публичном результате. Helm сначала запускает Privacy, затем C/P/Q с промежуточным backfill в режиме a011. Chart допускает новый namespace только при явном `syntheticBenchmark.enabled=true`; прежний namespace и default остаются прежними.

Перед нагрузкой проверяются владельцы и UID Pod, образы и ресурсы. На каждый Pod открывается loopback port-forward под Windows Job. Во время работы guard повторно проверяет Pod UID/ресурсы, владельца listener, local cluster и budget; собираются реальные `/metrics`, `kubectl top`, сводка причин events и lag из диагностик Processor. Отсутствие обязательных метрик останавливает прогон. Readiness/privacy/transport ошибки, смена Pod или пропущенный heartbeat также останавливают измерение. Нагрузка использует реальный HTTP и общий oracle; никаких TTL для фактов не устанавливается.

До каждой внешней mutation пишется отдельный pending record, после наблюдения результата — confirmed. Перед CREATE новой БД её конкретные имя и sentinel записываются в приватный owner record; временный файл записи сбрасывается через flush и fsync до `os.replace`. Сохранность записи самого directory entry после внезапной потери питания Windows не доказана: этот механизм не объявляется power-loss durable. Timeout или потерянный ответ означает неизвестный исход: автоповтора нет. Глобальный `.local/benchmark-k3s.active.json`, owner record и синтетические ресурсы остаются для ручной сверки при отказе процесса. Нельзя удалять marker и повторять кейс без reconciliation первоначальных UID, DB, Helm release и journal. После полного успешного прогона runner останавливает только собственные Deployments с UID/resourceVersion precondition и удаляет свой binder Pod и Job с UID precondition через принадлежащий процессу localhost kubectl proxy. Namespace, release, Secrets, БД, факты и journal PVC сохраняются; автоматически не удаляются retained v2 или Core. Если остановка не подтверждена, marker остаётся.

Результат содержит временные границы, counts/latency, oracle, фактические Pod UID/образы/ресурсы, срезы Pod metrics/events и ресурсный бюджет. Один кейс не доказывает три повтора и не проверяет privacy/replay/deletion/backup под полной нагрузкой: даже успешный обычный кейс оставляет общий статус `UNRUN`. Ошибка наблюдаемого контракта может быть `FAIL`; неполный прогон — `UNRUN`. Для обобщения нужны все независимые повторы и остальные сценарии профиля. Никакой оценки production capacity до реальных измерений нет.

## Подготовка образа для локального v2: команда и границы

**План, не исполнение и не GO.** Source history теперь содержит head `a011` и сохраняет исходный `a010` как проверяемый префикс. Исторический stopped checkpoint и порядок допуска/запуска тех же контейнеров — в [run-k3s.md](run-k3s.md); текущее состояние нужно проверить заново, а не выводить из этого снимка.

До любых действий после отдельного GO: аттестовать local Docker context/endpoint без вывода credentials; точные ID server container, его K3s image и retained authority; private `services/analytics/.local/k3s-kubeconfig-v2.yaml`, context `k3d-knowi-analytics-01a1df75-v2`, UID `kube-system` и hash kubectl. Выполнить `run_k3s.require_shared_local_budget(...)` с текущим `authority_image_id`, корректным состоянием Web и дополнительными заданными координатором memory/disk floors; проверить Core/Web, swap/pressure и свободный диск с запасом на архив. Проверить исходный fingerprint, полный head `a011`, целевой префикс и отсутствие незавершённого owner/active marker. Если что-то не совпало — STOP.

При разрешённом окне после точной проверки host Docker `.Id` argv последующих операций такие; `<host-id>` может быть OCI index ID, это **не** config digest. Аргументы подставляются из проверенного текущего состояния, без registry/push и без повторного создания кластера:

```text
docker --context <local-context> image inspect <host-id> --format {{.Id}}
docker --context <local-context> image save --platform linux/amd64 <host-id>
docker --context <local-context> exec -i <exact-server-id> ctr -n k8s.io images import --platform linux/amd64 -
docker --context <local-context> exec <exact-server-id> ctr -n k8s.io images list
docker --context <local-context> exec <exact-server-id> ctr -n k8s.io content get sha256:<observed-digest>
```

Это **argv, не безопасный PowerShell pipeline для копирования**. До первого node import есть отдельный wrapper, который готовит архив без node reference, registry и `RepoDigests`; запуск требует своего GO на export и probe, заданных `ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB` / `ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB` и резерва сверх disk floor на полный cap. Без флага команда только печатает offline план:

```powershell
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/prepare_k3s_image.py
# Только после отдельного GO на export и source probe:
$env:ANALYTICS_BENCHMARK_IMAGE_PREPARE_GO = '1'
$env:ANALYTICS_BENCHMARK_IMAGE_PROBE_GO = '1'
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/prepare_k3s_image.py --prepare-image --operation-id <new-uuid-v4> --host-image-id sha256:<immutable-host-id> --target-revision a011 --archive-max-mib 1024
```

Допустимый cap 128–1024 MiB; больших архивов эта стадия не принимает. Перед export создаются новый private owner directory, exclusive active и UUID intent с flush/fsync файла. `image save` ограничен временем и размером; проверяются имя и SHA config, linux/amd64, entrypoint, UID, рабочий каталог и diff IDs, но слои не извлекаются. Затем из доверенного собственного образа по immutable host ID выполняется изолированный source probe. Результат и архив остаются рядом с owner record, их путь выводится после успеха; node import и attestation в результате `UNRUN`. Directory-entry durability при потере питания Windows не заявляется. При timeout, обрыве или ошибке active marker, partial archive и pending records сохраняются: сначала reconciliation точного operation UUID, файлов и процессов, затем отдельное решение; автоматического повтора нет.

Import получает проверенный архив как binary stdin и требует отдельного GO для точного stopped v2 и нового budget guard, со своим pending marker и timeout. На timeout/lost reply сохранить архив и marker, сначала сверить exact containerd refs/content и процессы, не повторять import и не удалять ресурсы. Способ import и получения canonical manifest alias зависит от фактической версии `ctr`: до операции проверить её `images import --help` и поддерживаемые флаги. Не считать index manifest и не создавать alias blind retag. Сначала перечислить содержимое и подтвердить manifest SHA → config SHA и `rootfs.diff_ids` из сохранённого host proof, затем отдельно подтвердить применимый version-specific способ canonical alias; registry/push не используются.

После import list/content дают кандидаты для ручной сверки index → `linux/amd64` manifest → config. Index не подменяет manifest. Runner требует, чтобы `ctr images list` содержал именно `docker.io/library/knowi-analytics@sha256:<manifest>` с target тем же manifest digest; если alias отсутствует или указывает на index — STOP. Не создавать alias через произвольный tag и не считать импорт PASS до проверки version-specific способа его получения. После идентификации `verify_node_manifest`/`_image_attestation` повторно хэшируют node manifest и config; `verify_host_archive` сравнивает с ним config и `rootfs.diff_ids` архива. Внутри 20-минутного `--run` эти проверки выполняются повторно.

Конкретная read-only сверка **после** разрешённого import и нового budget GO, без `--run` и без измерительной нагрузки. В окружение заранее передаются `PYM_KUBECTL`, `PYM_KUBECTL_SHA256`, `PYM_K3S_IMAGE_ID`, `PYM_RETAINED_AUTHORITY_ID` (или `none`), `PYM_SERVER_ID`, `PYM_CLUSTER_UID`, `PYM_WEB_PAUSED` (`0` или `1`), `PYM_IMAGE_REF`, `PYM_CONFIG_ID`, `PYM_ARCHIVE`, `PYM_ARCHIVE_MAX_MIB`; значения должны быть получены из сверенной цели/owner record, а не из старого checkpoint. Установка `PYM_NODE_ATTEST_GO=1` допустима только в этом отдельно одобренном окне.

```powershell
$check = @'
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, 'services/analytics/tools')
import benchmark_k3s as b, run_k3s as r, bootstrap_runtime as history
e = os.environ
if e.get('PYM_NODE_ATTEST_GO') != '1' or e['PYM_WEB_PAUSED'] not in ('0', '1'):
    raise SystemExit('STOP: отдельный GO и состояние Web обязательны')
b.verified_histories(history, 'a010')
b.verified_histories(history, 'a011')
args = SimpleNamespace(kubectl=b._binary(e['PYM_KUBECTL'], e['PYM_KUBECTL_SHA256']),
    kubectl_sha256=e['PYM_KUBECTL_SHA256'], k3s_image_id=e['PYM_K3S_IMAGE_ID'],
    retained_authority_image_id=None if e['PYM_RETAINED_AUTHORITY_ID']=='none' else e['PYM_RETAINED_AUTHORITY_ID'],
    web_temporarily_paused=e['PYM_WEB_PAUSED']=='1', server_id=e['PYM_SERVER_ID'], cluster_uid=e['PYM_CLUSTER_UID'],
    image_ref=e['PYM_IMAGE_REF'], image_config_id=e['PYM_CONFIG_ID'],
    authority_ref=e['PYM_IMAGE_REF'], authority_config_id=e['PYM_CONFIG_ID'])
kube = b.Kube(args.kubectl, r.KUBECONFIG, r.CONTEXT, 'knowi-analytics')
budget = b._assert_target(args, r, kube, None)
node = b._image_attestation(args, r)['application']
raw = Path(e['PYM_ARCHIVE'])
local, private = r.ROOT / '.local', r.ROOT / '.local/cross-service'
if any(path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
       for path in (local, private, raw.parent, raw)):
    raise SystemExit('STOP: небезопасный путь архива')
archive = raw.resolve(strict=True)
private_root = private.resolve(strict=True)
if (local.resolve(strict=True).parent != r.ROOT.resolve(strict=True)
        or private_root.parent != local.resolve(strict=True) or archive.parent.parent != private_root):
    raise SystemExit('STOP: архив вне private owner directory')
max_mib = int(e['PYM_ARCHIVE_MAX_MIB'])
if not 128 <= max_mib <= 8192:
    raise SystemExit('STOP: недопустимый лимит архива')
with archive.open('rb') as stream:
    proof = b.verify_host_archive(stream, node['config_digest'], node['rootfs_diff_ids'], max_mib * 1024 * 1024)
print(json.dumps({'source': b._source_fingerprint(r.ROOT), 'budget': budget, 'node': node, 'archive': proof}))
'@
& 'services/analytics/.venv/Scripts/python.exe' -B -c $check
```

Минимальный отдельный smoke без measured case — после своего GO и нового budget guard: offline `helm template` и тесты; затем, только при подтверждённых host/node config и отдельном image-probe GO, ограниченный `_source_probe` по immutable host ID с новым owner/pending record. Он не создаёт benchmark DB, Pod и нагрузку. Результат source probe не доказывает CNI, готовность приложения, correctness HTTP, отсутствие расхода ресурсов или performance. На момент этой записи реальный Docker 29 archive → v2 import → canonical manifest alias и smoke **UNRUN**.
