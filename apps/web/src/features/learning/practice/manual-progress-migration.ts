// Lesson 70 originally shipped its third task without requirements. Adding that
// missing field must not erase the learner's existing local checkmarks.
export function lesson70PreviousSignature(lessonId: string, signature: string): string | undefined {
  if (lessonId !== "lesson-c4878f92ca23a93b777d9fa8") return undefined;
  try {
    const practice = JSON.parse(signature);
    const task = practice?.[0]?.tasks?.[2];
    if (!Array.isArray(practice) || practice.length !== 1 ||
        practice[0]?.tasks?.length !== 3 ||
        task?.title !== "Штатная сборка остаётся JSON" || !Array.isArray(task.requirements)) {
      return undefined;
    }
    delete task.requirements;
    return JSON.stringify(practice);
  } catch {
    return undefined;
  }
}
