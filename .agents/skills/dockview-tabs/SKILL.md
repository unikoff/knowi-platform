---
name: dockview-tabs
description: Добавляй и исправляй панели React/Dockview в Pymentor с устойчивыми ID, ограничениями DnD и совместимым восстановлением layout.
---

# Панели Dockview и сохранённая рабочая область

Применяй для вкладок, групп, перемещений и persistence рабочей области. Чистый backend или обычная страница без Dockview не требуют skill.

## Найти контракт рабочей области

Установи версии React, `dockview-react` и `dockview-core` по lockfile/установленным пакетам; найди регистрацию компонентов, команды, ID и сериализацию. Онлайн-документацию сверяй с типами/исходником установленной версии; не добавляй опцию нового релиза по памяти. Для Pymentor проверь существующие `WorkspaceWorkbench.tsx` и `layout.ts`, если они есть в текущей ревизии.

Установи поведение панели: singleton или несколько экземпляров, данные/маршрут, позиция, close/reopen, move/reorder/split/float/popout. Используй стабильный семантический ID, не title, индекс, случайное значение или время. Для singleton активируй существующую панель; для нескольких экземпляров следуй принятому ключу сущности. Переименование title не меняет ID. Изменение схемы ID требует обработки прежних сохранений.

## Ограничить действия точными механизмами

Различай drop-target группы, источник drag, запрет DnD всей области, resize grid и программные команды. `group.locked` не является универсальным запретом перемещения/закрытия: смысл значений зависит от версии. В подтверждённом API 8.5.1 `true` запрещает center/tabs drop, но допускает edge split; `'no-drop-target'` отключает все drop-зоны группы. `disableDnd` отключает dragging, а не любые программные `moveTo`/close.

Для выборочных правил используй поддерживаемые отменяемые события, например `onWillDragPanel`, `onWillDragGroup`, `onWillDrop`. Одно скрытие overlay не доказывает запрет drop. Проверь отдельную вкладку и целую группу, tab reorder и edge split отдельно. Не включай floating/popout и не меняй backend permissions как побочный эффект.

## Сохранить жизненный цикл и layout

Инициализируй панели после ready существующим способом, без дублей при повторном mount/StrictMode. Для добавленных подписок вызывай `dispose()` в cleanup; убирай DOM listeners и debounce timers при смене API/владельца и unmount. Не уничтожай API React-компонента вручную без подтверждённого контракта владения.

Перед `fromJSON` используй текущую валидацию/миграцию: версия формата, зарегистрированные components, допустимые ID, параметры и доступ пользователя. Учитывай owner и desktop/mobile ключи; старый debounce не должен записать layout другого владельца. Во время восстановления не сохраняй промежуточную пустую раскладку поверх валидной. Повреждённое/несовместимое сохранение направляй в штатный fallback; не очищай всё хранилище. `toJSON`/`fromJSON` не заменяют хранение кода редактора и не являются авторизацией.

## Подтвердить поведение

Проверь затронутые типы и UI: повторное открытие без дубля; close/reopen; разрешённые и запрещённые drag пути; save/reload; старый/повреждённый layout; смена owner/доступа и compact режима, когда затронуты. Сохрани состояние независимых панелей. Проверь keyboard focus и доступность close. Скриншот подтверждает вид, интерактивная проверка подтверждает DnD; одно другим не заменяется.

**Результат:** изменение, стабильные ID/миграция при необходимости, поддержанные/запрещённые действия, фактические проверки. Без браузера UI/DnD остаются NOT_VERIFIED, даже если build прошёл.

Опора: [disableDnd](https://dockview.dev/docs/core/dnd/disable/), [DnD hooks](https://dockview.dev/docs/core/dnd/overview/), [locking](https://dockview.dev/docs/core/locked/). Конкретные значения проверяй в установленном API.

## Delegated execution

Делегируй выделенную панель/interaction boundary с существующими ID и layout contracts. Верни changed paths и проверки восстановления/DnD; общий workspace state интегрирует root.

Effective delegation policy comes from the task contract resolved against
C:/Users/Unik/.codex/orchestrator.toml. Workers follow that scope without widening
the model pool/limits or starting orchestration recursively.
