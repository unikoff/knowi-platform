import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const FILTER_EXAMPLE = `from sqlalchemy import select

all_ids = select(EquipmentRequest.id)
open_ids = all_ids.where(EquipmentRequest.is_closed == False)

print(sorted(session.scalars(all_ids).all()))
print(sorted(session.scalars(open_ids).all()))`;

const ORDER_EXAMPLE = `newest_first = open_ids.order_by(EquipmentRequest.id.desc())

print(session.scalars(newest_first).all())
print(session.scalars(newest_first.limit(1)).all())`;

const INPUT_EXAMPLE = `for note in ("  ЛАМПА  ", "   ", "  КРЕПЁЖ_%  "):
    print(repr(note.strip().lower()))`;

const BADGE_EXAMPLE = `def label_for_request(number):
    if number < 1:
        return "Номер должен быть положительным"
    return f"Заявка {number}"

print(label_for_request(0))`;

const POSITION_EXAMPLE = `from sqlalchemy import func

position = func.instr(EquipmentRequest.caption_lower, "'x'")
found = position > 0
diagnosis = select(EquipmentRequest.id, position, found).order_by(EquipmentRequest.id)
print(session.execute(diagnosis).all())`;

const PRACTICE_DATA = `from importlib import reload

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.DBase import repositories
from app.DBase.models import Base, TaskRow

sample = [
    (42, "Отчёт 10X", True),
    (35, "Отчёт 10% ноябрь", False),
    (12, "План 'А'", True),
    (27, "План_Б", False),
    (53, "План Б", False),
]
engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
lab_session = Session(engine)
lab_session.add_all([
    TaskRow(
        id=task_id, title=title, is_done=is_done,
        priority=2, tags=[], title_normalized=title.lower(),
    )
    for task_id, title, is_done in sample
])
lab_session.commit()
engine.echo = True`;

const PRACTICE_RELOAD = `repo = reload(repositories).SqlTaskRepository(lab_session)
print([task.id for task in repo.list()])`;

export function Lesson73({ module }: { module?: string } = {}) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Отбираем нужные строки в SQL"
        intro="Когда список задач растёт, можно попросить SQLite вернуть только нужные записи. Посмотрим, как задать это условие и какие строки вернутся."
      />

      <Section number="00" title="Вступление">
        <Lead>В SQL-репозитории Planner уже можно прочитать задачи. Теперь представим два запроса: показать первые открытые задачи и найти названия с определённым фрагментом.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "SQLite возвращает строки, а репозиторий превращает их в объекты Task." },
          { title: "Новый вопрос", description: "Как объяснить базе, какие строки подходят и какие из них должны идти первыми?" },
          { title: "Результат", description: "Получим нужные задачи по статусу и найдём буквальный фрагмент в их названиях." },
        ]} />
        <p>Проследим оба запроса на реестре заявок на оборудование. Строки останутся теми же, а условие будет меняться.</p>
      </Section>

      <Section number="01" title="Как условие оставляет нужные строки">
        <Lead>Фильтр отвечает на вопрос о каждой строке. Если нам нужны открытые заявки, SQLite оставит строки, где отметка о закрытии равна <code>False</code>.</Lead>
        <p>Представим четыре карточки на стойке склада. Когда просят показать открытые заявки, сотрудник смотрит на отметку каждой карточки.</p>
        <table>
          <caption>Реестр заявок на оборудование</caption>
          <thead><tr><th scope="col">id</th><th scope="col">Название</th><th scope="col">Закрыта</th></tr></thead>
          <tbody>
            <tr><th scope="row">10</th><td>Лампа</td><td>Да</td></tr>
            <tr><th scope="row">9</th><td>Крепёж AX</td><td>Нет</td></tr>
            <tr><th scope="row">11</th><td>Крепёж 'X'</td><td>Нет</td></tr>
            <tr><th scope="row">8</th><td>КРЕПЁЖ_%</td><td>Да</td></tr>
          </tbody>
        </table>
        <p>
          В примерах <code>EquipmentRequest</code> обозначает ORM-модель этого реестра, <code>is_closed</code> хранит
          отметку о закрытии, а открытая <code>session</code> читает подготовленные строки. Разберём запросы прямо на
          странице. В проекте возьмём вашу модель <code>TaskRow</code> и знакомую Session репозитория.
        </p>
        <p>
          <code>select</code> указывает, что читаем. Здесь это только номера заявок. <code>where</code> добавляет
          условие для SQLite. Сравнение ORM-колонки через <code>==</code> описывает вопрос к строке. Вызов
          {" "}<code>where</code> возвращает новое описание запроса, поэтому сохраняем результат отдельно.
        </p>
        <CodeBlock code={FILTER_EXAMPLE} caption="Одна таблица, два запроса: все номера и номера открытых заявок" />
        <p>
          Первый вывод <code>[8, 9, 10, 11]</code> содержит все номера. Второй вывод <code>[9, 11]</code> содержит
          только номера карточек без отметки. Запрос выполняется в <code>session.scalars(...)</code>, а
          {" "}<code>.all()</code> даёт список. Здесь <code>sorted</code> нужен для удобства сравнения напечатанных
          номеров. Порядок внутри запроса зададим отдельно.
        </p>
        <p>
          Если сотруднику сказали «только открытые», значение <code>False</code> передаёт точное пожелание. Если
          про статус вообще не спрашивали, аргумент будет <code>None</code>. Для <code>False</code> проверка
          {" "}<code>if value</code> тоже не сработает. Чтобы отличить эти случаи, сравниваем само значение с
          {" "}<code>None</code>. Предположите, какие номера останутся, если поменять отметку в условии на <code>True</code>.
        </p>
        <details>
          <summary>Сверить прогноз для закрытых карточек</summary>
          <p>Пройдут заявки 8 и 10. У обеих есть отметка о закрытии.</p>
        </details>
        <p>
          В Planner список собирается из ORM-строк. В имеющемся <code>list()</code> уже видны
          {" "}<code>select(TaskRow)</code> и преобразование <code>task_from_row</code>. Для настройки статуса пригодится
          тот же способ прочитать результат.
        </p>
      </Section>

      <Section number="02" title="Порядок строк и предел выдачи">
        <Lead>Сортировка решает, какая из подходящих строк стоит первой. Предел отвечает, сколько строк взять с начала упорядоченного списка.</Lead>
        <p>
          Сотрудник оставил карточки 9 и 11. Если просят показать сначала заявку с большим номером, он кладёт
          11 перед 9. Если просят одну карточку, он берёт 11.
        </p>
        <p>
          <code>order_by</code> задаёт ключ сортировки. Здесь ключом будет номер. <code>desc()</code> ставит
          большие номера первыми, <code>asc()</code> ставит меньшие. Вызов <code>limit(1)</code> оставляет в
          результате до одной строки. Оба метода создают новое описание запроса.
        </p>
        <CodeBlock code={ORDER_EXAMPLE} caption="Из открытых заявок берём одну с наибольшим номером" />
        <p>
          Получаем <code>[11, 9]</code> без предела и <code>[11]</code> с пределом. Какой номер станет первым, если передать в <code>order_by</code> выражение
          {" "}<code>EquipmentRequest.id.asc()</code>? Если же начать с минимального номера всего реестра,
          попадётся закрытая 8.
        </p>
        <details>
          <summary>Сверить прогноз для возрастания</summary>
          <p>Первой станет 9. С пределом 1 получим <code>[9]</code>.</p>
        </details>
        <Diagram caption="Как из реестра получилась одна открытая заявка">
          <DiagramFlow label="От исходных заявок к первому номеру в выдаче">
            <DiagramNode title="Все заявки" shape="database"><p>10, 9, 11, 8</p></DiagramNode>
            <DiagramArrow label="Оставляем открытые" />
            <DiagramNode title="Подходят"><p>9 и 11</p></DiagramNode>
            <DiagramArrow label="По убыванию id" />
            <DiagramNode title="Порядок"><p>11, 9</p></DiagramNode>
            <DiagramArrow label="Берём одну" />
            <DiagramNode title="Ответ"><p>11</p></DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          Схема помогает прочитать смысл результата. В реальном следе SQL ищем <code>WHERE</code>,
          {" "}<code>ORDER BY</code> и, если задан предел, <code>LIMIT</code>. Верные номера могут получиться и
          после среза готового списка в Python. Предложение <code>LIMIT</code> в выполненном SQL показывает, что
          число строк ограничила база. SQLAlchemy может дописать рядом <code>OFFSET 0</code>, это чтение без пропуска строк.
        </p>
        <p>
          В репозитории отсутствие предела обозначается <code>None</code>. Обычный CLI-список запрашивает все
          задачи. HTTP по умолчанию просит 10 записей и принимает значения от 1 до 50. Эти границы уже проверяет
          HTTP-слой.
        </p>
      </Section>

      <Section number="03" title="От ввода к буквальному совпадению">
        <h3>Подготовка строки поиска</h3>
        <p>
          Нормализовать ввод значит привести его к одному виду перед сравнением. При поиске по записке складской
          сотрудник пропускает зазоры по краям и читает слово независимо от регистра букв. В Python
          {" "}<code>strip()</code> снимает крайние пробелы, а <code>lower()</code> переводит буквы в нижний регистр.
        </p>
        <CodeBlock code={INPUT_EXAMPLE} caption="Смотрим на записки отдельно, до запроса к таблице" />
        <p>
          <code>repr</code> показывает границы строки в кавычках. Получаем <code>'лампа'</code>, затем
          {" "}<code>''</code> и <code>'крепёж_%'</code>. Видно, что у пустой записки нет фрагмента, а знаки
          {" "}<code>_</code> и <code>%</code> остались на месте. Пробелы внутри слова или фразы тоже сохраняются.
        </p>
        <p>Иногда решение уже известно по входу. Кладовщик отклоняет номер 0 сразу, без обращения к реестру.</p>
        <CodeBlock code={BADGE_EXAMPLE} caption="Ранний return завершает обработку заявки с недопустимым номером" />
        <p>
          Видим «Номер должен быть положительным». После первого <code>return</code> функция не формирует подпись заявки.
          В Planner решение об остановке принимаем по подготовленному тексту. Когда фрагмент пуст, поиск возвращает
          пустой список до SELECT.
        </p>

        <h3>Буквальный фрагмент в запросе</h3>
        <p>
          Буквальный поиск ищет подряд написанные символы. На карточке 11 можно обвести фрагмент
          {" "}<code>'X'</code> вместе с двумя кавычками. У карточки 9 тоже есть X, но кавычек рядом нет.
        </p>
        <p>
          В SQLite функция <code>instr(строка, фрагмент)</code> возвращает позицию первого совпадения.
          Позиции начинаются с 1. Если фрагмента нет, результат равен 0. Для пустого фрагмента SQLite возвращает 1, поэтому его отсеиваем раньше. SQLite получает заранее заполненное
          название в нижнем регистре из <code>caption_lower</code>.
        </p>
        <CodeBlock code={POSITION_EXAMPLE} caption="Смотрим позицию и ответ на вопрос, найден ли фрагмент" />
        <p>
          <code>func.instr</code> описывает вызов SQL-функции. <code>execute</code> сохраняет все три колонки
          запроса, поэтому для каждой строки видны номер, позиция и результат сравнения с нулём. У 8, 9 и 10
          позиция 0 и ответ <code>False</code>. У «крепёж 'x'» открывающая кавычка стоит на восьмой позиции,
          ответ <code>True</code>. Если оставить только совпадения, в выдаче будет одна заявка 11.
        </p>
        <p>
          SQLAlchemy отправляет строковый аргумент отдельно от текста SQL. В следе у выражения
          {" "}<code>instr(..., ?) &gt; ?</code> виден знак <code>?</code> на месте значения. Среди параметров
          увидим <code>"'x'"</code> и порог <code>0</code>. Так кавычки остаются частью
          искомого текста. В Planner для сравнения уже есть колонка <code>title_normalized</code>. Она хранит
          результат Python <code>title.lower()</code>, в том числе для кириллицы.
        </p>
        <p>
          Знак <code>%</code> тоже читается буквально. Для него <code>instr</code> даст положительную позицию
          в «крепёж_%» и ноль в «крепёж ax». Для сравнения, в шаблоне <code>LIKE '%'</code> тот же знак
          означает последовательность любой длины, а <code>_</code> в шаблоне означает один произвольный знак.
          У Planner ввод <code>%</code> или <code>_</code> ищет именно написанный символ.
        </p>
        <QuizCard
          question="Что увидим для двух записок « _ » и «   »?"
          description="Возьмём тот же реестр. Сначала определите подготовленный фрагмент, затем решите, выполнится ли запрос."
          options={[
            "Первая даст [8] после запроса, вторая даст [] без запроса",
            "Обе дадут [] без запроса",
            "Первая даст все номера, вторая даст [] после запроса",
          ]}
          orientation="vertical"
          correctIndex={0}
          explanation="После обработки первой записки останется знак _. Он есть в названии 8, поэтому позиция только там положительная. Из второй записки получится пустая строка. Поиск завершается до SELECT."
        />
        <p>
          Для непустой записки без совпадений ответ тоже может быть пустым. В этом случае след содержит SELECT.
          Для пустой записки его нет. Так мы видим, на каком шаге получился результат.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>Вернёмся в SQL-репозиторий собственного Planner. По одному смешанному набору будет видно, что изменилось после каждой правки.</Lead>
        <LinkedNotes items={[
          { title: "Отберём по статусу", description: "Сравним открытые задачи со всеми задачами при отсутствии фильтра." },
          { title: "Упорядочим и ограничим", description: "С пределом увидим первые id в каждом направлении, а в следе SQL найдём ORDER BY и LIMIT." },
          { title: "Подготовим поисковый ввод", description: "Одинаковые по смыслу записки дадут одну строку. Для пробелов поиск закончится до SELECT." },
          { title: "Найдём буквальный фрагмент", description: "Из двух похожих названий останется то, где действительно напечатан нужный знак." },
        ]} />
        <p>Сначала проверим каждый результат вызовом репозитория. Так будет понятно, где искать причину, если пользовательская выдача отличается от ожидаемой.</p>
        <h3>Проверка в одной сессии Python</h3>
        <p>
          Откроем <code>python</code> из каталога <code>project/</code> в вашем обычном окружении Planner.
          Подготовим данные для всех четырёх карточек один раз. Адрес <code>sqlite://</code> держит базу в
          памяти этого процесса. Здесь используется знакомая ORM-модель Planner. После наполнения включим
          {" "}<code>engine.echo</code>, чтобы видеть SQL во время проверки.
        </p>
        <CodeBlock code={PRACTICE_DATA} caption="Учебные строки для проверки методов в Python" />
        <p>
          Перед первой задачей вызовем существующий <code>list()</code>. Строка <code>print</code> покажет
          знакомый список номеров <code>[12, 27, 35, 42, 53]</code>. Сообщения перед ним показывают SQL
          и отдельно параметры. Если список другой, проверим, какой проект открыт и все ли строки
          подготовлены.
        </p>
        <CodeBlock code={PRACTICE_RELOAD} caption="Получаем репозиторий и проверяем готовые данные" />
        <p>
          После правки файла снова выполним первую строку этого фрагмента. <code>reload</code> перечитает
          сохранённый модуль, а новый репозиторий получит ту же учебную Session. Дальше выполним вызовы из
          текущей карточки. В них список в квадратных скобках показывает номера возвращённых Task.
          После последней карточки закроем проверку командами <code>lab_session.close()</code> и
          {" "}<code>engine.dispose()</code>.
        </p>
      </Section>
    </RichLesson>
  );
}
