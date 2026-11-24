import {
  CodeBlock,
  Diagram,
  DiagramTrack,
  InteractiveTable,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "@course-ui";

const JOURNAL_LOOKUPS = `from io import StringIO

def borrow_journal():
    return StringIO("8: Кабель\\n9: Микрофон")

open_journal = borrow_journal

with open_journal() as first:
    print(first.readline().strip())

print("Правила выдачи на стойке")

with open_journal() as second:
    print(second.readlines()[1].strip())`;

const INTERRUPTED_REPORT = `from io import StringIO

observations = []

def signal_failure(journal):
    observations.append((journal, journal.closed))
    raise RuntimeError("Отчёт прерван")

def make_report(journal):
    print(journal.read())
    signal_failure(journal)

with StringIO("9: Микрофон") as first:
    print(first.read())

try:
    with StringIO("8: Кабель") as second:
        make_report(second)
except RuntimeError as error:
    observed, was_closed = observations[0]
    print(error)
    print("Первый закрыт:", first.closed)
    print("Сохранён второй:", observed is second)
    print("Второй закрыт до и после сбоя:", was_closed, observed.closed)`;

export function Lesson74({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Открываем Session на одну команду CLI"
        intro="Поиск уже закончился, а меню снова ждёт ввода. Где к этому моменту должна быть Session?"
      />

      <Section number="00" title="Вступление">
        <Lead>В HTTP ресурс нужен на время одного запроса. Меню CLI остаётся открытым и после того, как команда закончила работу.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "В пробной SQL-сборке Planner уже умеет читать и изменять задачи и считать сводку." },
          { title: "Новый вопрос", description: "Когда открывать Session, если ввод может оказаться опечаткой или командой выхода?" },
          { title: "Результат", description: "Команда с данными получает свою Session. Проверим её освобождение после обычного ответа и при сбое." },
        ]} />
        <p>Проследим путь от ввода до ответа и следующего приглашения. Так увидим, какой части команды нужен ресурс.</p>
      </Section>

      <Section number="01" title="Когда команде нужен ресурс">
        <p><strong>Фабрика контекста</strong> здесь означает функцию, которую можно вызвать, когда понадобится ресурс. На стойке выдачи сотрудник берёт журнал для поиска заявки и возвращает его после чтения. На вопрос о правилах можно ответить, даже если журнал лежит на полке.</p>
        <p>В примере <code>StringIO</code> хранит в памяти две строки журнала и позволяет прочитать их как файл. В <code>open_journal</code> сохраняем функцию без скобок. Каждый последующий вызов внутри <code>with</code> выдаёт свой журнал.</p>
        <CodeBlock code={JOURNAL_LOOKUPS} />
        <p>На экране по порядку появляются «8: Кабель», памятка о правилах и «9: Микрофон». Первый журнал закрывается при выходе из первого <code>with</code>. Памятка находится между областями, поэтому для неё журнал не открывается.</p>

        <p>Если посетитель назовёт неизвестный номер, журнал всё равно понадобится: отсутствие заявки выясняется при чтении. Опечатку в команде или уход можно распознать по самому вводу.</p>
        <QuizCard
          question="Когда понадобится журнал?"
          description="Посетитель спрашивает о правилах, просит заявку 77, затем уходит. В журнале есть только заявки 8 и 9."
          options={[
            "На всё время разговора от вопроса до ухода.",
            "На время поиска заявки 77.",
            "Журнал не понадобится: заявки 77 в нём нет.",
          ]}
          correctIndex={1}
          explanation="Отсутствие заявки выясняется при чтении. Вопрос о правилах и уход обходятся без журнала."
        />
        <p>В Planner <code>run</code> получает ввод, а <code>execute_command</code> выясняет, какой обработчик запускать. <code>read</code> отвечает на запросы ввода, <code>write</code> получает текст, который CLI собирается вывести. Состояние на следующем приглашении можно снять внутри <code>read</code>, когда он получил <code>Команда:</code> и ещё не вернул новый ввод. Запрос номера относится к работе уже выбранного поиска.</p>
      </Section>

      <Section number="02" title="Как доказать закрытие при исключении">
        <p><strong>Исключение</strong> прерывает работу и передаёт причину вызывающему коду. На стойке журнал «Микрофон» уже вернули. Для отчёта о кабеле взяли другой журнал. Когда из подготовки отчёта приходит сигнал о сбое, отметим, какой журнал сейчас у сотрудника и закрыт ли он.</p>
        <CodeBlock code={INTERRUPTED_REPORT} />
        <p><code>make_report</code> передаёт свой журнал во вложенный вызов <code>signal_failure</code>. Перед исключением тот сохраняет ссылку и отдельное значение <code>closed</code>. Снаружи <code>with</code> выражение <code>observed is second</code> подтверждает, что сохранили второй объект. У него <code>False</code> до сбоя и <code>True</code> после перехвата. Первый журнал был закрыт ещё до отчёта. Если второй оставить открытым, сообщение «Отчёт прерван» всё равно появится.</p>

        <Diagram caption="Между открытием журнала и завершением работы">
          <DiagramTrack label="Успешный поиск" items={["Взяли журнал", "Нашли заявку", "Вернули журнал"]} />
          <DiagramTrack label="Прерванный отчёт" items={["Взяли журнал", "Сбой", "Вернули журнал", "Перехватили ошибку"]} activeIndex={2} />
        </Diagram>

        <InteractiveTable
          title="Что подтверждает наблюдение"
          columns={["Наблюдение", "Вывод"]}
          rows={[
            { id: "error", label: <code>RuntimeError</code>, values: ["Отчёт прервался."] },
            { id: "other", label: "Предыдущий журнал вернули", values: ["Завершилась работа с предыдущим журналом."] },
            { id: "current", label: <><code>observed is second</code> и состояние до и после</>, values: ["Тот же журнал отчёта был открыт в момент сбоя и закрылся при выходе."] },
          ]}
        />

        <QuizCard
          question="Какое наблюдение подтвердит возврат журнала прерванного отчёта?"
          description="Поиск кабеля закончился, и сотрудник вернул его журнал. Затем он взял другой журнал для отчёта о шлеме. Во время отчёта возникло исключение."
          options={[
            "Сотрудник сообщил, что отчёт прервался.",
            "Закрыт журнал предыдущего поиска.",
            "После сбоя закрыли журнал отчёта о шлеме.",
          ]}
          correctIndex={2}
          orientation="vertical"
          explanation="Нужен журнал отчёта о шлеме. Сообщение описывает исключение, а закрытие журнала поиска относится к предыдущей работе."
        />

        <p>В Planner обработчик вызывает <code>write</code> и передаёт туда текст. Экземпляры Session уже фиксирует наблюдатель из прежней проверки. В момент сбоя найдите в нём Session текущей команды и сравните её отметку до исключения и после выхода из <code>run</code>. Свойство <code>closed</code> относится к файловому примеру.</p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Очертим ресурс команды", description: "Подключим контекст к выбранной команде и найдём известную задачу." },
          { title: "Сверим последовательность", description: "Предскажем, какие вводы получат Session, и сверим границы на смешанном сценарии." },
          { title: "Проверим закрытие на исключении", description: "Отличим завершённую Session первого поиска от Session прерванного поиска." },
        ]} />
      </Section>
    </RichLesson>
  );
}
