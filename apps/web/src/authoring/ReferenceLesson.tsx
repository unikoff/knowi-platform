import {
  Diagram, DiagramArrow, DiagramFlow, DiagramNode,
  RichLesson, RichHero, Section, Lead, QuizCard, StepThrough,
} from "@course-ui";

// Author examples only: no catalog entry, practice answers or learner progress.
export function ReferenceLesson() {
  return <RichLesson>
    <RichHero chip="ОБРАЗЦЫ ДЛЯ АВТОРА" title="Связь, состояние и проверка понимания" intro="Выбираем форму по затруднению ученика. Эти независимые примеры не являются новым занятием курса." />
    <Section number="01" title="Два исхода условия">
      <Lead>Программа печатает сообщение, когда температура ниже нуля. На границе, при нуле, строгое условие temperature &lt; 0 ложно. Сопоставим правило с двумя исходами.</Lead>
      <Diagram caption="Одно условие, два различимых исхода" orientation="vertical" className="lesson-diagram--full-width">
        <DiagramFlow orientation="vertical" label="Условие истинно">
          <DiagramNode title="temperature = -1"><p>Проверяем temperature &lt; 0</p></DiagramNode>
          <DiagramArrow label="Истина" />
          <DiagramNode title="Ветка выполняется"><p>Печатаем «Ниже нуля»</p></DiagramNode>
        </DiagramFlow>
        <DiagramFlow orientation="vertical" label="Условие ложно">
          <DiagramNode title="temperature = 0"><p>Проверяем temperature &lt; 0</p></DiagramNode>
          <DiagramArrow label="Ложь" />
          <DiagramNode title="Ветка пропускается"><p>Сообщение не печатается</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
      <QuizCard question="Что изменится при замене < на <= для temperature = 0?" options={["Ветка выполнится: равенство включено", "Ветка всё ещё пропускается"]} correctIndex={0} explanation="Условие <= допускает равенство. Ноль равен границе, поэтому ветка выполнится. При -1 обе записи истинны." />
    </Section>
    <Section number="02" title="Один объект с двумя именами">
      <Lead>После second = first оба имени указывают на один список. Изменение элемента через second видно через first. Присваивание не создаёт копию списка.</Lead>
      <StepThrough code={'first = [2, 4]\nsecond = first\nsecond[0] = 9'} steps={[
        { line: 0, note: "Создаём список", vars: { first: "[2, 4]" } },
        { line: 1, note: "Второе имя указывает на тот же список", vars: { first: "[2, 4]", second: "[2, 4]" } },
        { line: 2, note: "Меняем общий объект", vars: { first: "[9, 4]", second: "[9, 4]" } },
      ]} />
    </Section>
    <Section number="03" title="Запрос и ответ">
      <Lead>Клиент отправляет запрос серверу. Сервер обрабатывает его и возвращает ответ клиенту. Ниже показан порядок событий: две карточки клиента обозначают одного участника до запроса и после ответа.</Lead>
      <Diagram caption="Запрос уходит к серверу, ответ возвращается клиенту" orientation="vertical" className="lesson-diagram--full-width">
        <DiagramFlow orientation="vertical" label="Порядок запроса и ответа">
          <DiagramNode title="Клиент отправляет"><p>Запрашивает состояние</p></DiagramNode>
          <DiagramArrow label="Запрос к серверу: GET /status" />
          <DiagramNode title="Сервер обрабатывает"><p>Готовит ответ о состоянии</p></DiagramNode>
          <DiagramArrow label="Ответ клиенту: 200" />
          <DiagramNode title="Тот же клиент получает"><p>{'{"status": "ok"}'}</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
    </Section>
  </RichLesson>;
}
