# Библиотека визуала занятий

Канонический API — `@course-ui` (`courses/shared/ui/index.ts`). Course-local
`frontend/course/shared.ts` может реэкспортировать его, сохраняя импорты авторских
страниц. API сам загружает `components/styles/index.css`; отдельный список CSS
в странице или галерее не нужен. Каталог возможностей доступен через
`/backend/demo`, его исходники находятся в `courses/shared/demo`.

`ContentList` — связанный список; `ContentList ordered` — нумерованный.
`LinkedNotes` предназначен для секции 00 и завершающей секции. `Callout` принимает
`accent`, `success` (зелёный), `warn` (жёлтый, по умолчанию), `danger` (красный).
Для собственного цвета передайте CSS-цвет через `color`, например
`<Callout color="#8dbfe3">Текст</Callout>`; он имеет приоритет над `tone`.
Вариантов оформления обложки и связанного списка нет.
Авторская схема может иметь собственную геометрию и CSS-класс. Общие классы
библиотеки меняются только в `components/styles/`.

## Метаданные курса

`LessonMetadataProvider` принимает `source`, необязательные `module`, `title`,
`resolveMetadata` и `children`. Resolver имеет сигнатуру
`(source: string, module?: string, title?: string) => LessonMetadata | null`.
`LessonMetadata` содержит `block`, `blockNumber`, `lessonNumber` и необязательный
массив `tags`. Course entry экспортирует resolver как `getLessonMetadata`.
Названия блоков, legacy-нумерация и тематические теги принадлежат курсу.

Без resolver используется нейтральный fallback по source/module/title. Явный
результат `null` сохраняется: обложка может использовать свои `chip` и `tags`.
Shared provider не выбирает курс и не хранит metadata между рендерами.

## Тема и проверки

Shared UI наследует цвета от platform theme. Палитра, синтаксические цвета и
семантические статусы определены в `apps/web/src/shared/theme/palettes.ts`.
`@platform/*` ссылается на `apps/web/src/*`; через него библиотека использует
существующие CodeBlock, VisualHeading и подсветку Python. Shared demo импортирует
настоящий theme bootstrap и platform tokens/base CSS.

Из корня репозитория:

```powershell
node tooling/courses/check_lesson_visuals.cjs
node tooling/courses/test_lesson_visuals.cjs
node tooling/courses/test_course_ui.cjs
node courses/shared/ui/metadata.test.mjs
```

Первый скрипт проверяет course frontend TSX, владение общим CSS, семантические
переменные и отсутствие literal colors в shared CSS. SSR-проверка рендерит оба
курса и все варианты галереи; она не заменяет browser-проверку интерактивов,
computed styles, светлой/тёмной темы и размеров viewport.
