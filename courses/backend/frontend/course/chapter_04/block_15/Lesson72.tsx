import {
  CodeBlock,
  InteractiveTable,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const STATUS_EXAMPLE = `import json

previous = True
for body in ('{}', '{"is_done": null}', '{"is_done": false, "title": null}'):
    form = json.loads(body)
    value = form.get("is_done")
    result = previous if value is None else value
    print("Ключ есть:", "is_done" in form, "| Статус:", result)`;

const ORDERS_EXAMPLE = `def send_orders(orders):
    for field, value in orders:
        print("Передано на запись:", field, value)

print("Пустой бланк")
send_orders([])

print("Бланк с отметкой «не завершена»")
send_orders([("статус", False)])`;

export function Lesson72({ module }: { module?: string } = {}) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Различаем пропуск, null и False в PATCH"
        intro="Задача уже закрыта, а клиент хочет открыть её снова. Разберём, как принять False и сохранить остальные поля."
      />

      <Section number="00" title="Вступление">
        <Lead>После PUT мы видели новую версию задачи. В PATCH клиент может прислать только часть полей.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "По номеру задачи мы уже умеем найти и заменить её в SQLite." },
          { title: "Новый вопрос", description: "Откуда берётся значение, если поле пропустили, передали null или указали false?" },
          { title: "Результат", description: "False откроет задачу, а запрос без новых значений вернёт её без UPDATE." },
        ]} />
        <p>Начнём с бланка правки заявки на оборудование.</p>
      </Section>

      <Section number="01" title="Какие значения просит изменить PATCH">
        <Lead>Заявка 9 на проектор закрыта. Диспетчер получает бланк, в котором клиент может попросить открыть её снова.</Lead>
        <p>
          <code>PATCH</code> передаёт частичную правку. Если графы статуса в бланке нет, оставляем прежний статус.
          Явное <code>null</code> говорит, что графа есть, но по договору Planner новое значение не задано.
          Отметка <code>false</code> означает «не завершена». Для закрытой заявки это просьба открыть её снова.
        </p>
        <p>
          <code>json.loads</code> читает JSON как словарь Python. <code>null</code> превращается в <code>None</code>,
          а <code>false</code> в <code>False</code>. Проверим наличие ключа через <code>in</code>, а значение прочтём
          через <code>get</code>. Он вернёт <code>None</code>, если ключ отсутствует.
        </p>
        <CodeBlock code={STATUS_EXAMPLE} caption="Три бланка для одной закрытой заявки" />
        <InteractiveTable
          title="Что получилось при исходном True"
          columns={["Тело запроса", "Графа и итог"]}
          rows={[
            { id: "missing", label: <code>{"{}"}</code>, values: [<>Ключа нет. Статус остался <code>True</code>.</>] },
            { id: "null", label: <code>{'{"is_done": null}'}</code>, values: [<>Ключ есть, значение <code>None</code>. Статус остался <code>True</code>.</>] },
            { id: "false", label: <code>{'{"is_done": false, "title": null}'}</code>, values: [<>Ключ есть, значение <code>False</code>. Заявка открылась.</>] },
          ]}
        />
        <p>
          Проверка <code>value is None</code> оставляет исходный статус для первых двух бланков. В третьем получаем
          новое значение <code>False</code>. Если выбирать через <code>value or previous</code>, этот бланк тоже
          даст <code>True</code>: <code>or</code> принимает <code>False</code> за ложное значение.
        </p>
        <p>
          В знакомом маршруте Planner фильтр <code>exclude_unset</code> убирает неприсланные поля, а <code>exclude_none</code> убирает
          поля с <code>None</code>. В смешанном бланке до сервиса доходит только <code>is_done=False</code>.
          В словаре <code>{'{"is_done": False}'}</code> вызов <code>get("is_done", True)</code> вернёт <code>False</code>:
          запасное значение используется лишь для отсутствующего ключа.
        </p>
      </Section>

      <Section number="02" title="Когда запрос обходится без записи">
        <Lead>Диспетчер находит заявку по номеру. Если в бланке нет новых значений, у него нет поручений на запись.</Lead>
        <p>
          Действие без изменения называют <em>no-op</em>. Сравним два списка поручений для проектора.
          В первом нет ни одной записи. Во втором одна команда на открытие заявки. Печать заменяет передачу поручения на запись.
        </p>
        <CodeBlock code={ORDERS_EXAMPLE} caption="Какие поручения дошли до записи" />
        <p>
          После «Пустой бланк» передачи на запись нет: цикл по пустому списку не выполняется.
          Для второго бланка видим <code>Передано на запись: статус False</code>. В списке есть поручение, хотя значение внутри него равно <code>False</code>.
        </p>

        <QuizCard
          question="Что увидит диспетчер после правки приоритета?"
          description={<>У закрытой заявки на кабель приоритет 2. Приходит <code>{'{"priority": 3}'}</code>. Предскажем статус, приоритет и число поручений.</>}
          options={[
            "Закрыта, приоритет 3, одно поручение",
            "Открыта, приоритет 3, одно поручение",
            "Закрыта, приоритет 2, ни одного поручения",
          ]}
          correctIndex={0}
          explanation="Клиент задал новый приоритет, поэтому есть одно поручение. Графу статуса пропустили, и заявка остаётся закрытой."
        />

        <p>
          В Planner вспомним «Одна Session для изменения и транзакции» и «Что сохраняется при замене».
          Рабочая область охватывает первый поиск и возможное сохранение на той же <code>Session</code>.
          Даже с пустым телом сначала выясняем, существует ли задача. Если она найдена, её текущее состояние уже можно вернуть.
        </p>
        <p>
          Для проверки PATCH возьмём доработанную SQL-подмену из занятия «Создаём и заменяем задачу в SQLite».
          В её поставщике репозиторий получает <code>Session</code> запроса, а область записи открывает транзакцию на той же <code>Session</code>.
          На подготовленном <code>engine</code> включим журнал команд через <code>engine.echo = True</code>.
        </p>
        <p>
          Заявка на проектор уже закрыта. При пустой правке сервис сразу возвращает найденную заявку.
          Если он по ошибке поручит репозиторию сохранить прежний <code>True</code>, SQLAlchemy может пропустить <code>UPDATE</code>,
          поскольку значение совпало. В обоих случаях в журнале будет ноль <code>UPDATE</code>.
          Чтобы подтвердить ранний возврат, проследим в коде, доходит ли пустая ветка до вызова <code>update</code>.
        </p>
        <p>
          В журнале пустой правки может быть <code>SELECT</code>. Сравним его с запросом, который действительно меняет статус и даёт
          <code>UPDATE</code> к таблице <code>tasks</code>. После опыта вернём прежнее значение <code>engine.echo</code>.
        </p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Применим частичную правку", description: "Свежая Session покажет новый статус и прежние остальные данные." },
          { title: "Проверим пустой запрос", description: "На одном исходном статусе сравним два запроса без записи и один запрос с UPDATE." },
        ]} />
      </Section>
    </RichLesson>
  );
}
