import { useState, type ReactNode } from "react";
import { LessonMetadataProvider } from "../ui/metadata-context";
import {
  RichHero, Section, Callout, LessonScreenshots, TypeCards, TypeCard,
  ContentList, LinkedNotes, Checklist, QuizCard, RecallCard, FlipCards, MatchPairs,
  FillBlank, CodeSequence, CodeBlock, PredictOutput, StepThrough,
  BranchExplorer, SliceExplorer, InteractiveTable, Diagram, DiagramFlow,
  DiagramNode, DiagramArrow, DiagramGroup, DiagramLane, DiagramExchange,
  DiagramBranches, DiagramBranch, DiagramTrack, DiagramNote, DiagramLegend,
} from "../ui";
import { defineCatalog, type CatalogVariant } from "./catalog";

const demo = (id: string, title: string, content: ReactNode): CatalogVariant => ({ id, title, content });
const examplePhoto = new URL("./assets/example-portrait.jpg", import.meta.url).href;
const photoDescription = <><p>Фотография может дополнять рассказ, не прерывая текст. Она занимает немного места, а строки располагаются рядом с ней и продолжаются ниже.</p><p>Так удобно познакомить читателя с человеком, показать место или добавить иллюстрацию к небольшому объяснению. На узком экране изображение располагается над текстом, чтобы всё оставалось удобным для чтения.</p><p>Например, в истории о команде портрет помогает увидеть человека, о котором идёт речь. Рядом можно рассказать, чем он занимается и почему его опыт важен для темы занятия.</p><p>Подпись или ссылка на источник остаётся рядом с фотографией. Если нужно рассмотреть детали, изображение можно открыть крупнее и затем вернуться к чтению.</p></>;
const examplePhotoItem = { src: examplePhoto, alt: "Портрет человека в тёмной одежде", title: "Пример портрета", description: photoDescription, sourceUrl: examplePhoto, sourceLabel: "Открыть фотографию" };
const addition = "total = 2\ntotal = total + 3\nprint(total)";

function CustomColorCallout() {
  const [color, setColor] = useState("#8dbfe3");
  return <div>
    <label>Свой цвет подсветки <input type="color" value={color} onChange={event => setColor(event.target.value)} /></label>
    <Callout color={color}>Выберите цвет для этого смыслового акцента.</Callout>
  </div>;
}

// Curated demonstrations of the public library. Do not import lessons here.
export const catalogGroups = defineCatalog([
  { id: "layout", title: "Оформление", examples: [
    { id: "hero", title: "Обложка занятия", api: "RichHero", kind: "visual", variants: [
      demo("hero-standard", "Блок, название и темы занятия", <LessonMetadataProvider source="chapter_01/block_01/Lesson01"><RichHero title="Как запускается программа" intro="Напишем короткую команду, запустим её и увидим результат. Потом прочитаем команды по порядку и потренируемся исправлять ошибки." /></LessonMetadataProvider>),
    ] },
    { id: "section", title: "Заголовок и абзацы", api: "Section", kind: "visual", variants: [
      demo("section-typography", "Связный текст с разным оформлением", <Section number="01" title="Знакомый город по-новому">
        <p>Знакомый район легко пройти по привычке: свернуть у магазина, пересечь площадь и сразу направиться дальше. Но однажды можно выбрать более спокойный темп и посмотреть на тот же маршрут внимательнее. Утренний свет меняет цвет домов, во дворе открывается небольшая пекарня, а за обычным поворотом обнаруживается тихий переулок.</p>
        <p><strong>Обратите внимание на детали.</strong> Отражения в витринах, рисунок старой ограды и запах свежего хлеба складываются в отдельную историю. Иногда именно <em>непривычная тишина</em> помогает заметить то, что раньше оставалось фоном. Для такой прогулки не нужен подробный план: достаточно любопытства и немного свободного времени.</p>
        <p><span className="lesson-emphasis">Короткий маршрут</span> может состоять всего из трёх остановок: небольшой площади, книжного магазина и скамейки у воды. Между ними остаётся место для случайных находок. Отметьте <strong><em>любимое место</em></strong>, чтобы вернуться к нему в другой день и сравнить впечатления.</p>
        <p>После прогулки удобно сохранить несколько заметок в файле <code>route.txt</code>: записать название улицы, время и одну запомнившуюся деталь. В черновике можно вычеркнуть <s>лишнюю остановку</s>, добавить новую мысль или оставить короткий вопрос. Эти записи не обязаны быть подробным отчётом — нескольких точных наблюдений вполне достаточно.</p>
        <p>Со временем из таких заметок получается личная карта города. В ней важны не расстояния и названия достопримечательностей, а знакомые ощущения и небольшие открытия. Если захочется повторить прогулку, можно <a href="#example-section">вернуться к началу описания</a>, выбрать другую улицу и снова оставить место для неожиданного.</p>
      </Section>),
    ] },
    { id: "callout", title: "Смысловой акцент", api: "Callout", kind: "visual", variants: [
      demo("callout-accent", "Акцентный цвет темы", <Callout tone="accent">Обратите внимание на эту мысль.</Callout>),
      demo("callout-danger", "Красный — опасное действие", <Callout tone="danger">Перед удалением данных убедитесь, что выбраны нужные записи.</Callout>),
      demo("callout-warn", "Жёлтый — важная проверка", <Callout tone="warn">Перед запуском проверьте входные данные и ожидаемый результат.</Callout>),
      demo("callout-success", "Зелёный — полезный вывод", <Callout tone="success">Меняйте одно условие за раз: так проще связать действие с результатом.</Callout>),
      demo("callout-custom", "Свой цвет", <CustomColorCallout />),
    ] },
    { id: "screenshots", title: "Изображение в тексте", api: "LessonScreenshots", kind: "interactive", variants: [
      demo("image-single", "Изображение с увеличением", <LessonScreenshots title="Фотография рядом с текстом" images={[examplePhotoItem]} />),
      demo("image-link", "Изображение со ссылкой", <LessonScreenshots title="Фотография со ссылкой" images={[{ ...examplePhotoItem, action: "link", sourceUrl: "#example-section", sourceLabel: "Перейти к примеру текста" }]} />),
      demo("image-static", "Изображение без действия", <LessonScreenshots title="Фотография в тексте" images={[{ ...examplePhotoItem, action: "none" }]} />),
    ] },
  ] },
  { id: "routes", title: "Списки и карточки", examples: [
    { id: "type-cards", title: "Карточки понятий", api: "TypeCards", kind: "visual", variants: [
      demo("concepts-code", "Понятия с примерами кода", <TypeCards><TypeCard badge="INT" title="Целое число" code="count = 3">Количество элементов.</TypeCard><TypeCard badge="FLOAT" badgeTone="float" title="Дробное число" code="price = 2.5">Значение с дробной частью.</TypeCard><TypeCard badge="STR" badgeTone="str" title="Строка" code={'name = "Аня"'}>Последовательность символов.</TypeCard></TypeCards>),
      demo("concepts-text", "Сопоставление понятий без кода", <TypeCards><TypeCard badge="01" title="Гипотеза">Что мы ожидаем увидеть после действия.</TypeCard><TypeCard badge="02" badgeTone="float" title="Наблюдение">Что произошло при проверке.</TypeCard><TypeCard badge="03" badgeTone="str" title="Вывод">Как наблюдение связано с исходным предположением.</TypeCard></TypeCards>),
    ] },
    { id: "content-list", title: "Список внутри объяснения", api: "ContentList", kind: "visual", variants: [
      demo("list-notes", "Связанные пояснения", <ContentList items={[{ title: "Вход.", description: "Данные, с которыми начинается работа." }, { title: "Действие.", description: "Одна операция над данными." }, { title: "Результат.", description: "Наблюдаемое изменение." }]} />),
      demo("list-ordered", "Нумерованная последовательность", <ContentList ordered items={[{ title: "Сформулировать гипотезу.", description: "Записать ожидаемый результат." }, { title: "Провести проверку.", description: "Изменить одно условие." }, { title: "Сравнить результаты.", description: "Объяснить совпадение или различие." }]} />),
      demo("list-companion", "Пояснение рядом с примером", <ContentList maxWidth="26rem" items={[{ title: "Исходное состояние.", description: "В списке два элемента." }, { title: "Добавление.", description: "Метод append добавляет ещё один элемент." }, { title: "Наблюдение.", description: "Длина списка увеличилась на один." }]}><CodeBlock caption="Изменение списка" code={'items = ["А", "Б"]\nitems.append("В")\nprint(len(items))'} /></ContentList>),
    ] },
    { id: "linked-notes", title: "Обзор и итоги", api: "LinkedNotes", kind: "visual", variants: [
      demo("overview-start", "Маршрут в начале занятия", <LinkedNotes items={[{ title: "Что уже известно", description: "Программа выполняет записанные инструкции." }, { title: "Новый вопрос", description: "Как проверить, что результат соответствует ожиданию?" }, { title: "Что попробуем", description: "Сделаем прогноз, запустим пример и сравним результаты." }]} />),
      demo("overview-end", "Итоги и следующий шаг", <LinkedNotes items={[{ title: "Что выяснили", description: "Наблюдение помогает проверить предположение." }, { title: "Что умеем", description: "Можем связать небольшое изменение с его результатом." }, { title: "Что дальше", description: "Проверим тот же приём на другом наборе данных." }]} />),
    ] },
    { id: "checklist", title: "Интерактивный чек-лист", api: "Checklist", kind: "interactive", variants: [
      demo("checklist-before", "Подготовка к работе", <Checklist title="Перед экспериментом" points={["Входные данные известны.", "Ожидаемый результат записан.", "Выбрано одно изменение для проверки."]} />),
      demo("checklist-after", "Проверка готового результата", <Checklist title="Можно двигаться дальше" points={[<>Код запускается без ошибки.</>, <>Результат <code>print()</code> совпал с прогнозом.</>, "Получилось объяснить причину результата."]} />),
    ] },
  ] },
  { id: "questions", title: "Вопросы и задания", examples: [
    { id: "quiz", title: "Выбор ответа", api: "QuizCard", kind: "interactive", variants: [
      demo("quiz-short", "Короткий вопрос", <QuizCard question="Что вернёт len([10, 20, 30])?" options={["2", "3", "30"]} correctIndex={1} explanation="В списке три элемента. Значения элементов не меняют его длину." />),
      demo("quiz-code", "Сравнение решений с кодом", <QuizCard question="Как посчитать элементы списка?" description="Выберите способ, который прямо выражает задачу." options={[{ title: "Готовая операция", code: "count = len(items)", description: "Намерение видно сразу." }, { title: "Ручной цикл", code: "count = 0\nfor item in items:\n    count += 1", description: "Тот же результат требует больше кода." }]} correctIndex={0} explanation="Для подсчёта элементов достаточно len." />),
      demo("quiz-error", "Поиск ошибки с последующим разбором", <QuizCard question="Почему этот код завершится с ошибкой?" description={<CodeBlock code={"items = []\nprint(items[0])"} />} orientation="vertical" options={["В пустом списке нет элемента с индексом 0", "print не работает со списками", "Имя items запрещено"]} correctIndex={0} explanation="Сначала нужно проверить, что список не пуст." answer={<CodeBlock caption="Один из вариантов исправления" code={"if items:\n    print(items[0])"} />} />),
      demo("quiz-boolean", "Правда или ложь", <QuizCard question="Правая граница среза входит в результат?" options={["Правда", "Ложь"]} correctIndex={1} explanation="В срезе text[start:end] символ с индексом end не включается." />),
      demo("quiz-accepted", "Несколько допустимых решений", <QuizCard question="Как проверить, что список items пуст?" description="Выберите один подходящий вариант." options={[{ title: "По истинности списка", code: "if not items:" }, { title: "По числу элементов", code: "if len(items) == 0:" }, { title: "По значению None", code: "if items is None:" }]} correctIndex={[0, 1]} explanation="Оба первых способа подходят. None — отдельное значение, не пустой список." />),
    ] },
    { id: "recall", title: "Вопрос с раскрытием ответа", api: "RecallCard", kind: "interactive", variants: [
      demo("recall-concept", "Вспомнить понятие своими словами", <RecallCard question="Чем гипотеза отличается от наблюдения?" answer="Гипотеза описывает ожидаемый результат, а наблюдение — то, что произошло при проверке." />),
      demo("recall-code", "Подсказка и ответ с примером", <RecallCard question="Чем return отличается от print?" hint="Кому достанется результат: человеку или вызывающему коду?" answer={<><p>Вывод видит человек. Возвращаемое значение получает вызывающий код.</p><CodeBlock code={"def double(value):\n    return value * 2\n\nresult = double(3)\nprint(result)"} /></>} />),
      demo("recall-prediction", "Прогноз результата кода", <RecallCard question="Что выведет программа?" code={addition} hint="Проследите, как меняется total." answer={<><p>Программа выведет <code>5</code>: к исходному значению 2 прибавляется 3.</p><CodeBlock language="text" code="5" /></>} />),
      demo("recall-prediction-existing", "Прогноз из существующего занятия", <PredictOutput code={'print("Первая строка")\nprint("  Вторая строка")'} output={"Первая строка\n  Вторая строка\n"} hint="Обратите внимание на пробелы перед второй строкой." />),
    ] },
    { id: "flip-cards", title: "Переворачиваемые карточки", api: "FlipCards", kind: "interactive", variants: [
      demo("flip-terms", "Термин и определение", <FlipCards cards={[{ front: "Аргумент", back: "Значение, которое передаём при вызове функции." }, { front: "Параметр", back: "Имя, которое принимает аргумент в определении функции." }]} />),
      demo("flip-examples", "Задача и короткий пример", <FlipCards cards={[{ front: "Узнать длину списка", back: <code>len(items)</code> }, { front: "Добавить элемент в список", back: <code>items.append("Новый")</code> }]} />),
    ] },
    { id: "match-pairs", title: "Сопоставление пар", api: "MatchPairs", kind: "interactive", variants: [
      demo("match-values", "Понятие и пример", <MatchPairs prompt="Соедините тип и значение." leftTitle="Тип" rightTitle="Значение" pairs={[{ left: "int", right: "42" }, { left: "str", right: '"привет"' }, { left: "bool", right: "True" }]} explanation="У каждого значения есть свой тип." />),
      demo("match-actions", "Операция и назначение", <MatchPairs prompt="Соедините действие с подходящим инструментом." leftTitle="Инструмент" rightTitle="Действие" pairs={[{ left: "len", right: "Посчитать элементы" }, { left: "append", right: "Добавить элемент" }, { left: "print", right: "Показать значение" }]} />),
    ] },
    { id: "fill-blank", title: "Заполнение пропуска", api: "FillBlank", kind: "interactive", variants: [
      demo("blank-method", "Выбрать операцию", <FillBlank prompt="Добавьте элемент в список." before={"items = []\nitems."} after={'("Новый")'} options={["append", "pop", "clear"]} answer="append" explanation="append добавляет элемент в конец списка." />),
      demo("blank-operator", "Подобрать условие", <FillBlank prompt="Порог пройден при результате 5 и выше." before="if score " after={' 5:\n    print("Готово")'} options={[">=", ">", "=="]} answer=">=" explanation="Оператор >= включает само пороговое значение." />),
      demo("blank-keyword", "Восстановить ключевое слово", <FillBlank prompt="Функция должна вернуть удвоенное значение." before={"def double(value):\n    "} after=" value * 2" options={["return", "print", "for"]} answer="return" explanation="return передаёт значение вызывающему коду." />),
    ] },
    { id: "sequence", title: "Сборка последовательности", api: "CodeSequence", kind: "interactive", variants: [
      demo("sequence-actions", "Порядок действий", <CodeSequence title="Проверяем гипотезу" prompt="Расположите действия по порядку." pieces={[{ id: "compare", code: "Сравнить результат с прогнозом" }, { id: "predict", code: "Записать ожидаемый результат" }, { id: "run", code: "Запустить пример" }]} correctOrder={["predict", "run", "compare"]} explanation="Сначала нужен прогноз, затем наблюдение и сравнение." />),
      demo("sequence-code", "Программа из отдельных строк", <CodeSequence title="Добавить элемент и вывести длину" prompt="Соберите работающую программу." pieces={[{ id: "print", code: "print(len(items))" }, { id: "create", code: "items = []" }, { id: "append", code: 'items.append("Новый")' }]} correctOrder={["create", "append", "print"]} explanation="Список нужно создать до изменения, а длину посчитать после добавления." />),
      demo("sequence-blocks", "Блоки кода и лишний шаг", <CodeSequence title="Определение и вызов функции" prompt="Выберите нужные блоки и расставьте их по порядку." pieces={[{ id: "call", code: "result = double(3)" }, { id: "print", code: "print(result)" }, { id: "define", code: "def double(value):\n    return value * 2" }, { id: "extra", code: "result = None", note: "Нужен ли этот шаг?" }]} correctOrder={["define", "call", "print"]} explanation="Сначала определяем функцию, затем получаем и выводим результат. Присваивание None не требуется." />),
    ] },
  ] },
  { id: "code", title: "Код и выполнение", examples: [
    { id: "code-block", title: "Код, команды и вывод", api: "CodeBlock", kind: "visual", variants: [
      demo("code-python", "Python с подсветкой синтаксиса", <CodeBlock caption="Определение и вызов функции" code={"def double(value):\n    return value * 2\n\nprint(double(3))"} />),
      demo("code-command", "Команды без подсветки Python", <CodeBlock caption="В терминале" language="text" code={"python demo.py\npython -m pytest"} />),
      demo("code-output", "Результат или структурированные данные", <CodeBlock caption="Пример ответа" language="text" code={'{\n  "count": 3,\n  "ready": true\n}'} />),
      demo("code-dialog", "Команды вместе с результатами", <CodeBlock caption="Сеанс в терминале" language="text" code={"$ Get-Location\nC:\\Demo\n\n$ python demo.py\nПривет!"} />),
    ] },
    { id: "step-through", title: "Пошаговое выполнение", api: "StepThrough", kind: "interactive", variants: [
      demo("trace-values", "Последовательное изменение переменной", <StepThrough code={addition} steps={[{ line: 0, note: "Создаём переменную.", vars: { total: "2" } }, { line: 1, note: "Прибавляем три к прежнему значению.", vars: { total: "5" } }, { line: 2, note: "Выводим итоговое значение: 5.", vars: { total: "5" } }]} />),
      demo("trace-loop", "Повторные проходы по строкам цикла", <StepThrough code={"total = 0\nfor value in [2, 3]:\n    total += value\nprint(total)"} steps={[{ line: 0, note: "Начинаем с нулевой суммы.", vars: { total: "0" } }, { line: 1, note: "Первое значение равно двум.", vars: { total: "0", value: "2" } }, { line: 2, note: "Добавляем первое значение.", vars: { total: "2", value: "2" } }, { line: 1, note: "Следующее значение равно трём.", vars: { total: "2", value: "3" } }, { line: 2, note: "Добавляем второе значение.", vars: { total: "5", value: "3" } }, { line: 3, note: "Цикл завершён. Выводим сумму: 5.", vars: { total: "5", value: "3" } }]} />),
    ] },
    { id: "branch", title: "Исследование ветвления", api: "BranchExplorer", kind: "interactive", variants: [
      demo("branch-two", "Два исхода условия", <BranchExplorer code={'if score >= 5:\n    print("Готово")\nelse:\n    print("Повторим")'} scenarios={[{ label: "score = 7", activeLine: 1, output: "Готово" }, { label: "score = 3", activeLine: 3, output: "Повторим" }]} />),
      demo("branch-three", "Цепочка if / elif / else", <BranchExplorer code={'if score >= 8:\n    print("Отлично")\nelif score >= 5:\n    print("Хорошо")\nelse:\n    print("Повторим")'} scenarios={[{ label: "score = 9", activeLine: 1, output: "Отлично" }, { label: "score = 6", activeLine: 3, output: "Хорошо" }, { label: "score = 2", activeLine: 5, output: "Повторим" }]} />),
    ] },
    { id: "slice", title: "Исследование среза", api: "SliceExplorer", kind: "interactive", variants: [
      demo("slice-middle", "Часть строки между границами", <SliceExplorer text="PYTHON" initialStart={1} initialEnd={4} />),
      demo("slice-prefix", "Начало строки", <SliceExplorer text="FILE_NAME" initialStart={0} initialEnd={4} />),
      demo("slice-empty", "Пустой диапазон", <SliceExplorer text="ABCDE" initialStart={3} initialEnd={3} />),
    ] },
  ] },
  { id: "diagrams", title: "Схемы и таблицы", examples: [
    { id: "interactive-table", title: "Таблица данных и сравнений", api: "InteractiveTable", kind: "interactive", variants: [
      demo("table-data", "Текст и числовые значения", <InteractiveTable title="Набор данных" columns={["Объект", "Количество", "Состояние"]} rows={[{ id: "a", label: "Группа А", values: [12, "Готово"] }, { id: "b", label: "Группа Б", values: [8, "В работе"] }, { id: "c", label: "Группа В", values: [15, "Запланировано"] }]} />),
      demo("table-compare", "Сравнение понятий", <InteractiveTable title="Три способа проверить результат" columns={["Способ", "Что проверяем", "Когда применять"]} rows={[{ id: "observe", label: "Наблюдение", values: ["Конкретный пример", "Первое знакомство"] }, { id: "assert", label: "Утверждение", values: ["Ожидаемое свойство", "Самопроверка"] }, { id: "test", label: "Тест", values: ["Повторяемый сценарий", "Защита от регрессий"] }]} />),
      demo("table-code", "Код внутри ячеек", <InteractiveTable title="Операции со списком" columns={["Операция", "Результат"]} rows={[{ id: "len", label: <code>len(items)</code>, values: ["Количество элементов"] }, { id: "append", label: <code>items.append(value)</code>, values: ["Добавление в конец списка"] }, { id: "slice", label: <code>items[1:3]</code>, values: ["Новый список из части элементов"] }]} />),
    ] },
    { id: "diagram-flow", title: "Конструктор схем", api: "Diagram", kind: "visual", variants: [
      demo("diagram-flow", "Горизонтальный поток", <Diagram caption="От входа к сохранённому результату"><DiagramFlow label="Поток обработки"><DiagramNode shape="terminator" title="Вход" /><DiagramArrow label="данные" /><DiagramNode title="Обработка">Преобразуем значение</DiagramNode><DiagramArrow label="результат" /><DiagramNode shape="database" title="Хранилище" /></DiagramFlow></Diagram>),
      demo("diagram-vertical", "Вертикальный процесс", <Diagram caption="Последовательность проверки" orientation="vertical"><DiagramFlow label="Этапы проверки" orientation="vertical"><DiagramNode shape="terminator" title="Начало" /><DiagramArrow label="" /><DiagramNode shape="subprocess" title="Проверить данные">Отдельная операция</DiagramNode><DiagramArrow label="" /><DiagramNode shape="terminator" title="Результат" /></DiagramFlow></Diagram>),
      demo("diagram-branches", "Условие и альтернативные исходы", <Diagram caption="Выбор дальнейшего действия" orientation="vertical"><DiagramNode shape="decision" title="Данные верны?" /><DiagramBranches label="Исходы проверки"><DiagramBranch label="Да"><DiagramNode title="Продолжить">Использовать данные</DiagramNode></DiagramBranch><DiagramBranch label="Нет"><DiagramNode title="Исправить">Показать причину ошибки</DiagramNode></DiagramBranch></DiagramBranches></Diagram>),
      demo("diagram-exchange", "Участники и обмен сообщениями", <Diagram caption="Запрос и ответ"><DiagramGroup title="Один цикл общения"><DiagramFlow label="Участники обмена"><DiagramLane title="Отправитель"><DiagramNode title="Клиент" /></DiagramLane><DiagramExchange forward="запрос" reverse="ответ" /><DiagramLane title="Получатель"><DiagramNode title="Сервис" /></DiagramLane></DiagramFlow></DiagramGroup></Diagram>),
      demo("diagram-track", "Этапы и текущее состояние", <Diagram caption="Движение по этапам"><DiagramTrack label="Состояние работы" items={["Подготовка", "Проверка", "Готово"]} activeIndex={1} /><DiagramNote title="Сейчас выполняется проверка">Активный этап выделен, остальные показывают путь до и после него.</DiagramNote></Diagram>),
      demo("diagram-links", "Направления связей и легенда", <Diagram caption="Связи между частями системы"><DiagramFlow label="Направления обмена"><DiagramNode title="Источник" /><DiagramArrow label="двусторонний обмен" direction="both" lineStyle="dashed" /><DiagramNode title="Обработчик" /><DiagramArrow label="обратная связь" direction="backward" /><DiagramNode title="Проверка" /></DiagramFlow><DiagramLegend items={[{ symbol: "↔", label: "Обмен в обе стороны" }, { symbol: "←", label: "Обратное направление" }]} /></Diagram>),
      demo("diagram-shapes", "Формы узлов", <Diagram caption="Роли элементов на схеме"><DiagramFlow label="Формы узлов"><DiagramNode shape="terminator" title="Начало / конец" /><DiagramArrow label="" /><DiagramNode title="Действие" /><DiagramArrow label="" /><DiagramNode shape="decision" title="Условие" /><DiagramArrow label="" /><DiagramNode shape="database" title="Данные" /><DiagramArrow label="" /><DiagramNode shape="subprocess" title="Подпроцесс" /></DiagramFlow></Diagram>),
    ] },
  ] },
]);
