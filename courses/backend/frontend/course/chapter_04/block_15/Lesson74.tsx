import {
  Callout,
  CodeBlock,
  Diagram,
  DiagramLane,
  DiagramTrack,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "@course-ui";

const REGISTER_EXAMPLE = `from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

engine = create_engine("sqlite://")
with engine.begin() as db:
    db.execute(text("CREATE TABLE requests (id INTEGER, title TEXT)"))
    db.execute(text(
        "INSERT INTO requests VALUES (8, 'Кабель'), "
        "(9, 'Микрофон'), (10, 'Шлем')"
    ))

class RegisterSession(Session):
    def close(self):
        super().close()
        print(f"close({self.info['mark']})")

make_session = sessionmaker(bind=engine, class_=RegisterSession)

def borrow(mark):
    session = make_session(info={"mark": mark})
    print(f"open({mark})")
    return session

def find_request(session, number):
    query = text("SELECT title FROM requests WHERE id = :number")
    return session.scalar(query, {"number": number})

open_register = borrow

with open_register("R1") as register:
    print(find_request(register, 9))

print("?: правила выдачи")

with open_register("R2") as register:
    print(find_request(register, 10))

print("stop: уходим")`;

const REPORT_EXAMPLE = `def prepare_report(register, number):
    mark = register.info["mark"]
    print(f"handler({mark})")
    title = find_request(register, number)
    print(f"fault({mark}): не получился отчёт по заявке «{title}»")
    raise RuntimeError(mark)

try:
    with open_register("R7") as register:
        prepare_report(register, 8)
except RuntimeError as error:
    print(f"caught({error})")`;

const PRACTICE_SETUP = `from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.cli import run
from app.DBase.models import Base
from app.main import sql_service_context

engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
seed_sessions = sessionmaker(bind=engine)
with sql_service_context(seed_sessions) as planner:
    task_id = planner.add_task("Запланировать звонок", 2).id

print(task_id)`;

const PRACTICE_TRACE = `active = []

class TraceSession(Session):
    opened = 0
    def __init__(self, **options):
        super().__init__(**options)
        TraceSession.opened += 1
        self.mark = f"S{TraceSession.opened}"
        active.append(self.mark)
        print(f"open({self.mark})")
    def close(self):
        super().close()
        active.remove(self.mark)
        print(f"close({self.mark})")

sessions = sessionmaker(bind=engine, class_=TraceSession)

def open_tasks():
    return sql_service_context(sessions)`;

const PRACTICE_FAULT = `def interrupt_find(prompt):
    if prompt == "Команда: ":
        return "find"
    mark = active[0] if active else "без метки"
    print(f"handler({mark})")
    print(f"fault({mark})")
    raise RuntimeError(mark)

try:
    run(None, read=interrupt_find, context_factory=open_tasks)
except RuntimeError as error:
    print(f"caught({error})")
    print(f"active={active}")`;

export function Lesson74({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Открываем Session на одну команду CLI"
        intro="Поиск уже закончился, а меню снова ждёт ввода. Где к этому моменту должна быть Session?"
      />

      <Section number="00" title="Вступление">
        <Lead>В HTTP у ресурса есть понятная граница: один запрос. В CLI меню может ждать новые команды сколько угодно.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Тестовый SQL-сервис Planner читает и изменяет задачи и умеет считать сводку." },
          { title: "Новый вопрос", description: "Когда открыть Session, если ввод может оказаться опечаткой или командой выхода?" },
          { title: "Результат", description: "У команды с данными будет своя Session. Мы увидим её закрытие после обычного ответа и при сбое." },
        ]} />
        <p>Проследим путь от приглашения до ответа и следующего приглашения. По нему удобно увидеть, где команда уже начала работу и где её ресурс пора отпустить.</p>
      </Section>

      <Section number="01" title="Когда команде нужен ресурс">
        <Lead>Session нужна той части работы, которая обращается к данным. Пока CLI ждёт название команды, эта работа ещё не началась.</Lead>
        <p>Представьте стойку выдачи оборудования. Для поиска микрофона сотрудник берёт журнал заявок и возвращает его после поиска. На вопрос о правилах можно ответить без журнала. Если посетитель уходит, журнал тоже остаётся на месте.</p>
        <p>В коде <strong>фабрика контекста</strong> даёт программе способ получить ресурс в нужный момент. В примере <code>open_register = borrow</code> сохраняет саму функцию. Вызов <code>open_register("R1")</code> создаёт Session с меткой R1. <code>with … as …</code> отдаёт её телу блока и закрывает при выходе.</p>
        <p>В начале примера готовим SQLite в памяти и три заявки: кабель, микрофон и шлем. Метод <code>close()</code> печатает метку после настоящего закрытия. В нижней части примера два поиска выполняются явно, а между ними можно ответить про правила. Весь пример можно запустить в Python-окружении Planner.</p>
        <CodeBlock code={REGISTER_EXAMPLE} />
        <Callout tone="accent">
          Поиск 9 выводит «Микрофон» между <code>open(R1)</code> и <code>close(R1)</code>. Реплика о правилах появляется после закрытия R1. Поиск 10 открывает R2, выводит «Шлем» и закрывает R2. Для ухода новых событий нет.
        </Callout>
        <p>Если попросить заявку 404, поиск всё равно возьмёт журнал. Узнать, что записи нет, можно только после чтения. Уход и опечатку программа может распознать по самому вводу.</p>
        <p>В Planner <code>run</code> ждёт команды, а <code>execute_command</code> различает выход, неизвестный ввод и найденный обработчик. Вы уже передаёте обработчикам <code>read</code> и <code>write</code> для ввода и вывода. При пробном SQL-запуске к этой цепочке добавится функция, которая открывает контекст с сервисом. Передавайте имя функции без скобок, если выбор момента вызова остаётся за другой частью программы.</p>
        <p>Метка в журнале обозначает экземпляр Session. Повторное использование соединения движком не делает две Session одной. У записи есть отдельная граница транзакции: подтверждение по-прежнему происходит в <code>write_scope</code> сервиса.</p>
      </Section>

      <Section number="02" title="Как доказать закрытие при исключении">
        <Lead>Сотрудник начал готовить отчёт по кабелю, но работа прервалась. О сбое сообщает одно событие. Возврат журнала подтверждает отметка с номером того же экземпляра.</Lead>
        <p><strong>Инъекция сбоя</strong> означает, что мы заранее выбираем место и вызываем там контролируемое исключение. Здесь отчёт читает заявку 8, затем отмечает сбой и прерывается. Выполните этот фрагмент в том же сеансе Python после примера с журналом. Внешний <code>except</code> увидит исключение после выхода из <code>with</code>.</p>
        <CodeBlock code={REPORT_EXAMPLE} />
        <p>Сначала видны <code>open(R7)</code> и <code>handler(R7)</code>. После <code>fault(R7)</code> Python выходит из блока. Метод <code>close()</code> успевает отметить <code>close(R7)</code>; затем внешний блок печатает <code>caught(R7)</code>. Если журнал показывает только сбой и перехват, по ним нельзя установить, вернулся ли журнал. Строка <code>close(R6)</code> свидетельствовала бы об освобождении другого экземпляра.</p>

        <Diagram caption="Когда журнал находится у команды">
          <DiagramLane title="Обычный поиск и вопрос о правилах">
            <DiagramTrack label="Поиск заявки 9" items={["open(R1)", "Микрофон", "close(R1)"]} />
            <DiagramTrack label="Ввод «?»" items={["Реплика о правилах", "Журнал свободен"]} />
            <DiagramTrack label="Поиск заявки 10" items={["open(R2)", "Шлем", "close(R2)"]} />
          </DiagramLane>
          <DiagramLane title="Отчёт с исключением">
            <DiagramTrack label="Порядок событий" items={["open(R7)", "handler(R7)", "fault(R7)", "close(R7)", "caught(R7)"]} activeIndex={3} />
          </DiagramLane>
        </Diagram>

        <QuizCard
          question="У каких действий будет журнал и какая строка подтвердит его возврат после сбоя?"
          description="Сначала посетитель спрашивает о правилах, потом ищет заявку 404. После этого сотрудник начинает отчёт по шлему и прерывается. Возможные метки идут по порядку: R8, затем R9."
          options={[
            "Журнал берут только для отчёта. Достаточно fault(R8).",
            "Журнал берут для поиска и для отчёта. Нужна строка close(R9) после сбоя.",
            "Журнал берут для поиска и для отчёта. Достаточно close(R8).",
          ]}
          correctIndex={1}
          orientation="vertical"
          explanation="Поиск 404 узнаёт об отсутствии заявки только после чтения с R8. Отчёт получает R9. Сбой и закрытие предыдущего журнала не подтверждают возврат R9."
        />
        <p>В проверочной консоли Planner сбой возникнет в <code>find</code>, когда обработчик запросит номер задачи. Строка <code>handler</code> отмечает этот запрос, а <code>active</code> перечисляет метки, которые ещё не закрылись. По соседним строкам журнала можно увидеть, какая Session закрылась.</p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>Подключим готовый SQL-контекст к существующему CLI Planner. Свои изменения будем проверять на временной SQLite.</Lead>
        <LinkedNotes items={[
          { title: "Очертим ресурс команды", description: "Для одного поиска появится отдельная пара открытия и закрытия." },
          { title: "Сверим последовательность", description: "Предскажем события для двух поисков, опечатки и выхода и сравним их с журналом." },
          { title: "Проверим закрытие на исключении", description: "Объясним по метке и порядку событий, что произошло с Session прерванного поиска." },
        ]} />
        <h3>Проверочная консоль Planner</h3>
        <p>До правки CLI откройте <code>python</code> в каталоге <code>project/</code> в обычном окружении Planner. Подготовьте одну задачу через знакомый SQL-сервис. База создаётся в памяти. Если консоль после блока показывает <code>...</code>, завершите блок пустой строкой. Запомните напечатанный id. Это вход для первого поиска и проверка того, что ваш SQL-сервис доступен.</p>
        <CodeBlock code={PRACTICE_SETUP} />
        <p>В этой же консоли настройте наблюдение. <code>TraceSession</code> помечает каждый созданный экземпляр, а при закрытии сначала вызывает настоящий <code>Session.close()</code>, затем убирает метку из <code>active</code>. Функция <code>open_tasks</code> использует фабрику вашего проекта. В обычных проверках вы вводите команды с клавиатуры. Начало ожидания видно по приглашению <code>Команда:</code>.</p>
        <CodeBlock code={PRACTICE_TRACE} />
        <p>После правки <code>app/cli.py</code> обновите только его импорт командами <code>from importlib import reload</code>, <code>import app.cli</code>, <code>reload(app.cli)</code>, <code>from app.cli import run</code>. Данные в консоли сохранятся. В первых двух карточках запускайте <code>run(None, context_factory=open_tasks)</code>. Здесь <code>None</code> означает, что готовый сервис отсутствует, а <code>open_tasks</code> передан для будущего вызова.</p>
        <h3>Сбой на запросе номера</h3>
        <p>Возьмите эту пробу в третьей карточке. При приглашении меню она подставит <code>find</code>. Когда обработчик попросит номер, проба запишет наблюдаемую метку и прервёт вызов. Перехват находится снаружи <code>run</code>, поэтому видно, какие метки остались на выходе.</p>
        <CodeBlock code={PRACTICE_FAULT} />
      </Section>
    </RichLesson>
  );
}
