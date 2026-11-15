import {
  CodeBlock,
  Lead,
  LinkedNotes,
  ContentList,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

// 5. Значение None в программе
export function Lesson05({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Блок 1 · Первые шаги в Python"}
        title="Значение None в программе"
        intro="Различим известное отсутствие, другие значения и имя, которое Python не находит."
      />

      <Section number="00" title="Когда ответ пока не задан">
        <Lead>
          В анкете поле для второго имени уже предусмотрено, хотя человек ещё мог его не заполнить.
          Программе нужно знать разницу между таким отсутствием и ответом в виде пустой строки или числа ноль.
        </Lead>
        <LinkedNotes
          items={[
            {
              title: "Знакомая опора",
              description: "Имя связывает программу со значением; сравнение даёт True или False.",
            },
            {
              title: "Новый вопрос",
              description: "Как явно обозначить, что значение пока не указано?",
            },
            {
              title: "Результат",
              description: "Проверить именно отсутствие и отличить его от похожих значений и ошибок.",
            },
          ]}
        />
        <p>
          В Python для явно отсутствующего значения есть <code>None</code>. Оно поможет отличить отсутствие ответа от других значений.
        </p>
      </Section>

      <Section number="01" title="None: имя существует, ответа пока нет">
        <Lead>
          Значение None сообщает об отсутствии данных у доступного имени. Имя при этом существует.
        </Lead>
        <p>
          None пишется без кавычек и с большой буквы N. Это специальное значение; запись
          <code>"None"</code> в кавычках была бы текстом из четырёх букв. В примере имя слева
          от знака <code>=</code> получает значение справа. Позднее новое присваивание может
          заменить его, когда ответ станет известен.
        </p>
        <CodeBlock code={"second_name = None\nprint(second_name)\nprint(type(second_name))"} />
        <ContentList
          items={[
            { title: "Имя", description: "second_name задано" },
            { title: "Значение", description: "None · ответ пока не указан" },
            { title: "Печатный результат", description: "None" },
            { title: "Вывод type(second_name)", description: "<class 'NoneType'>" },
          ]}
        />
        <QuizCard
          question="В анкете задано profile_label = None. Что можно сказать об этом имени?"
          options={[
            "Имя задано, его значение явно сообщает об отсутствии ответа",
            "Имя не существует, поэтому чтение обязательно даст NameError",
            "В profile_label хранится текст из слова None",
          ]}
          correctIndex={0}
          explanation="None: специальное значение доступного имени. Текст пришлось бы заключить в кавычки, а неизвестное имя вызвало бы другую ошибку."
        />
      </Section>

      <Section number="02" title="Задаём точный вопрос о None">
        <Lead>
          Проверка is None спрашивает, является ли значение именно None; is not None задаёт обратный вопрос.
        </Lead>
        <p>
          Читаем первую запись как вопрос «это None?», а вторую, с <code>not</code>, как обратный
          вопрос «это не None?». Результат каждой проверки имеет тип bool: True или False. Проверка
          ничего не присваивает и не выбирает действие. Для чисел и текста вопрос о равенстве
          по-прежнему записывают через <code>==</code>.
        </p>
        <CodeBlock code={"room = None\nprint(room is None)\nprint(room is not None)\n\nroom = 12\nprint(room is None)\nprint(room is not None)"} />
        <p>
          Сначала две команды print покажут True и False. После нового присваивания они покажут
          False и True: теперь в room записано число 12. Ни одна из проверок не меняет room.
        </p>
        <ContentList
          items={[
            { title: "room = None", description: "is None → True · is not None → False" },
            { title: "room = 12", description: "is None → False · is not None → True" },
            { title: "Результат проверки", description: "bool: True или False" },
            { title: "Само room", description: "Остаётся без изменений" },
          ]}
        />
        <QuizCard
          question={'В пропуске на мероприятие записано access_pass = "ready". Какие ответы дадут is None и is not None по порядку?'}
          options={[
            "False, затем True",
            "True, затем False",
            "None, затем True",
          ]}
          correctIndex={0}
          explanation="Здесь хранится текст, поэтому вопрос о None даёт False, а обратный вопрос даёт True. Значение при проверке не меняется."
        />
      </Section>

      <Section number="03" title="Пустое на вид не значит отсутствующее">
        <Lead>
          Ноль, False и пустая строка остаются значениями разных типов. None можно использовать,
          чтобы явно обозначить отсутствие ответа.
        </Lead>
        <p>
          Проверка is None не спрашивает, кажется ли значение пустым или ложным. Она проверяет только None.
          Поэтому результат False сам по себе не сообщает, что перед нами число или непустой текст.
        </p>
        <ContentList
          items={[
            { title: "None", description: "NoneType · ответ пока не указан" },
            { title: "0", description: "int · известное количество равно нулю" },
            { title: "False", description: "bool · известный отрицательный ответ" },
            { title: '""', description: "str · заданный текст без символов" },
          ]}
        />
        <QuizCard
          question="Количество мест ещё неизвестно. Какое значение поможет отличить это от точно известного нуля?"
          options={[
            "None: количество пока не указано",
            "0: количество точно равно нулю",
            "False: это булев ответ",
          ]}
          correctIndex={0}
          explanation="None показывает, что количество ещё не указано. Ноль означал бы известное количество; False и пустая строка тоже имеют собственный смысл."
        />
        <QuizCard
          question={'Для room_label = "north" проверка room_label is None даёт False. Что можно заключить?'}
          options={[
            "Проверенное значение не является None",
            "Имя неизвестно Python",
            "Проверка автоматически заменила текст на None",
          ]}
          correctIndex={0}
          explanation="False отвечает только на вопрос о None. Значение room_label остаётся прежним текстом."
        />
      </Section>

      <Section number="04" title="Доступное None и неизвестное имя">
        <Lead>
          Сначала Python должен найти имя в текущем запуске. Только после этого можно читать его значение.
        </Lead>
        <p>
          Если имя задано и ему присвоено None, чтение выводит None. Если имя не определено или написано
          с ошибкой, Python сообщает NameError. Ошибка чтения имени не является ещё одним значением.
        </p>
        <CodeBlock code={"visitor = None\nprint(visitor)"} />
        <CodeBlock code={"print(unknown_visitor)"} />
        <ContentList
          items={[
            { title: "visitor задан как None", description: "Заранее показанный вывод: None" },
            { title: "unknown_visitor не задан", description: "Заранее показан NameError" },
            { title: "unknown_visitor is None", description: "Сначала всё равно требуется найти имя" },
          ]}
        />
        <p>
          Это два отдельных примера: во втором имя unknown_visitor не задано. Проверка is None
          не исправляет опечатку и не создаёт имя. Например, если задано participant_name = "Lina",
          запись particpant_name с пропущенной буквой относится к другому имени.
        </p>
        <QuizCard
          question="Почему чтение particpant_name может закончиться NameError, если задано participant_name?"
          options={[
            "В написании прочитанного имени есть пропущенная буква",
            "participant_name автоматически имеет значение None",
            "NameError означает, что переменная содержит False",
          ]}
          correctIndex={0}
          explanation="Python ищет точное имя в текущем запуске. Похожее написание не связывает его с уже заданным participant_name."
        />
      </Section>

      <Section number="05" title="Почему None нельзя прибавить к числу">
        <Lead>
          Чтобы сложить величины как числа, нужны числовые значения. None не является числом и не заменяется нулём.
        </Lead>
        <p>
          При попытке сложить None и число возникает TypeError: для этих типов такая операция не подходит.
          Если число известно, его можно считать отдельно. Если данных нет, сначала сообщают об этом точной проверкой.
        </p>
        <CodeBlock code={"travel_duration = None\nprint(travel_duration + 5)"} />
        <CodeBlock code={"known_duration = 10\nprint(known_duration + 5)"} />
        <ContentList
          items={[
            { title: "travel_duration = None; + 5", description: "Заранее указан TypeError" },
            { title: "known_duration = 10; + 5", description: "Заранее указан результат 15" },
            { title: "travel_duration is None", description: "Отдельный ответ о наличии: True" },
          ]}
        />
        <p>
          Это отдельные примеры: в первом вычисление останавливается с ошибкой, во втором выводится 15.
          Нельзя молча подставить 0 вместо отсутствующего времени: это изменит смысл исходных данных.
          NameError из предыдущего раздела связан с поиском имени; TypeError здесь связан с неподходящей
          арифметической операцией над найденным значением.
        </p>
        <QuizCard
          question="Почему статический пример travel_duration = None и travel_duration + 5 помечен TypeError?"
          options={[
            "None не является числом для сложения",
            "Проверка is None вернула False",
            "Имя travel_duration не было задано",
          ]}
          correctIndex={0}
          explanation="Имя существует, но хранит None. Ошибка возникает из-за неподходящего типа операнда; известное число 10 можно сложить с 5 и получить 15."
        />
      </Section>

      <Section number="06" title="Практика и рекомендации при возврате">
        <Lead>
          В практике мы запишем проверки и расчёты на новых данных, а затем сравним результат с условием.
        </Lead>
        <p>
          Сначала обратим внимание на исходные значения в условии. При ошибке вернёмся к нужному
          объяснению, исправим решение и попробуем снова.
        </p>
        <LinkedNotes
          items={[
            { title: "Проверить значение", description: "Получить тип и ответы на оба вопроса о None." },
            { title: "Различить случаи", description: "Сопоставить отсутствие с нулём, False и пустой строкой." },
            { title: "Разобраться с ошибкой", description: "Проверить имя и отдельно рассчитать результат по известным числам." },
          ]}
        />
        <p>
          Проверка отсутствия даёт булев ответ. Само значение она не превращает в число, поэтому
          для расчёта понадобятся известные числовые данные.
        </p>
      </Section>
    </RichLesson>
  );
}
