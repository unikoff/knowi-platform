import React, { useState } from "react";
import { plainOptionText } from "./option-text";
import { Check, RotateCcw, X } from "lucide-react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

type CodeSequencePiece = {
  id: string;
  code: string;
  note?: string;
};

export function CodeSequence({
  prompt,
  pieces,
  correctOrder,
  explanation,
  title = "Расставляем действия",
  incorrectExplanation = "Проверьте, какой шаг должен выполняться раньше. Лишние шаги можно не добавлять.",
}: {
  prompt: string;
  pieces: CodeSequencePiece[];
  correctOrder: string[];
  explanation: string;
  title?: string;
  incorrectExplanation?: string;
}) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isChecked, setIsChecked] = useState(false);
  const selectedPieces = selectedIds.map((id) => pieces.find((piece) => piece.id === id)).filter(Boolean) as CodeSequencePiece[];
  const availablePieces = pieces.filter((piece) => !selectedIds.includes(piece.id));
  const isCorrect =
    selectedIds.length === correctOrder.length && selectedIds.every((id, index) => id === correctOrder[index]);

  function addPiece(id: string) {
    setSelectedIds((current) => [...current, id]);
    setIsChecked(false);
  }

  function removePiece(id: string) {
    setSelectedIds((current) => current.filter((pieceId) => pieceId !== id));
    setIsChecked(false);
  }

  function reset() {
    setSelectedIds([]);
    setIsChecked(false);
  }

  return (
    <section className="sequence-card">
      <VisualHeading kind="sequence" />
      {title !== "Расставляем действия" && <h3 className="sequence-title">{title}</h3>}
      <p className="interactive-prompt">{prompt}</p>

      <div className="sequence-workspace">
        <div className="sequence-panel">
          <span className="sequence-label">Шаги</span>
          <div className="sequence-pieces answer-options">
            {availablePieces.map((piece) => (
              <button className="sequence-piece answer-option" type="button" key={piece.id} onClick={() => addPiece(piece.id)}>
                <span className="sequence-text">{plainOptionText(piece.code)}</span>
                {piece.note && <small>{plainOptionText(piece.note)}</small>}
              </button>
            ))}
            {availablePieces.length === 0 && <p className="sequence-empty">Все шаги добавлены.</p>}
          </div>
        </div>

        <div className="sequence-panel sequence-panel--target">
          <span className="sequence-label">Наш порядок</span>
          <div className="sequence-target answer-options" aria-live="polite">
            {selectedPieces.length > 0 ? (
              selectedPieces.map((piece, index) => (
                <button
                  className="sequence-selected answer-option is-selected"
                  aria-pressed="true"
                  type="button"
                  key={piece.id}
                  onClick={() => removePiece(piece.id)}
                  aria-label={`Убрать шаг ${index + 1}: ${plainOptionText(piece.code)}`}
                >
                  <span className="answer-option-marker">{index + 1}</span>
                  <span className="sequence-text">{plainOptionText(piece.code)}</span>
                  <X size={14} />
                </button>
              ))
            ) : (
              <p className="sequence-empty">Выбираем шаги в нужном порядке.</p>
            )}
          </div>
        </div>
      </div>

      <div className="interactive-actions">
        <button className="interactive-secondary" type="button" onClick={reset} disabled={selectedIds.length === 0}>
          <RotateCcw size={15} /> Сбросить
        </button>
        <button className="interactive-primary" type="button" onClick={() => setIsChecked(true)} disabled={selectedIds.length === 0}>
          Проверить порядок
        </button>
      </div>

      {isChecked && (
        <p className={`interactive-feedback ${isCorrect ? "is-correct" : "is-wrong"}`}>
          {isCorrect ? <Check size={16} /> : <X size={16} />}
          {isCorrect ? explanation : incorrectExplanation}
        </p>
      )}
    </section>
  );
}
