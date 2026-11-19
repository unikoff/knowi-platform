import React, { useEffect, useRef, useState } from "react";
import { plainOptionText } from "./option-text";
import { Check, Plus, RotateCcw, X } from "lucide-react";
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
  const sourceButtons = useRef(new Map<string, HTMLButtonElement>());
  const checkButton = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<{ pieceId?: string } | null>(null);
  const selectedPieces = selectedIds.map((id) => pieces.find((piece) => piece.id === id)).filter(Boolean) as CodeSequencePiece[];
  const availablePieces = pieces.filter((piece) => !selectedIds.includes(piece.id));
  const isCorrect =
    selectedIds.length === correctOrder.length && selectedIds.every((id, index) => id === correctOrder[index]);

  useEffect(() => {
    if (!pendingFocus.current) return;
    const { pieceId = availablePieces[0]?.id } = pendingFocus.current;
    const target = pieceId === undefined ? checkButton.current : sourceButtons.current.get(pieceId);
    target?.focus();
    pendingFocus.current = null;
  });

  function addPiece(id: string) {
    setSelectedIds((current) => current.includes(id) ? current : [...current, id]);
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
          <div className="sequence-panel-heading">
            <div><span className="sequence-label">Доступные шаги</span><span className="sequence-hint">Нажмите, чтобы добавить</span></div>
            <span className="sequence-count" aria-label={`Доступно шагов: ${availablePieces.length}`}>{availablePieces.length}</span>
          </div>
          <div className="sequence-pieces answer-options">
            {availablePieces.map((piece) => (
              <button
                className="sequence-piece answer-option"
                type="button"
                key={piece.id}
                ref={element => {
                  if (element) sourceButtons.current.set(piece.id, element);
                  else sourceButtons.current.delete(piece.id);
                }}
                onClick={event => {
                  if (event.detail === 0) pendingFocus.current = {};
                  addPiece(piece.id);
                }}
              >
                <span className="sequence-piece-content">
                  <span className="sequence-text">{plainOptionText(piece.code)}</span>
                  {piece.note && <small>{plainOptionText(piece.note)}</small>}
                </span>
                <Plus className="sequence-piece-icon" size={16} aria-hidden="true" />
              </button>
            ))}
            {availablePieces.length === 0 && <p className="sequence-empty">Все шаги добавлены.</p>}
          </div>
        </div>

        <div className="sequence-panel sequence-panel--target">
          <div className="sequence-panel-heading">
            <div><span className="sequence-label">Ваш порядок</span><span className="sequence-hint">Нажмите, чтобы убрать</span></div>
            <span className="sequence-count" aria-label={`Выбрано шагов: ${selectedPieces.length}`}>{selectedPieces.length}</span>
          </div>
          <div className="sequence-target answer-options" aria-live="polite">
            {selectedPieces.length > 0 ? (
              selectedPieces.map((piece, index) => (
                <button
                  className="sequence-selected answer-option"
                  type="button"
                  key={piece.id}
                  onClick={event => {
                    if (event.detail === 0) pendingFocus.current = { pieceId: piece.id };
                    removePiece(piece.id);
                  }}
                  aria-label={`Убрать шаг ${index + 1}: ${plainOptionText(piece.code)}`}
                >
                  <span className="answer-option-marker">{index + 1}</span>
                  <span className="sequence-text">{plainOptionText(piece.code)}</span>
                  <X size={14} aria-hidden="true" />
                </button>
              ))
            ) : (
              <p className="sequence-empty">Выбираем шаги в нужном порядке.</p>
            )}
          </div>
        </div>
      </div>

      <div className="interactive-actions">
        <button className="interactive-secondary" type="button" onClick={event => {
          if (event.detail === 0) pendingFocus.current = {};
          reset();
        }} disabled={selectedIds.length === 0}>
          <RotateCcw size={15} aria-hidden="true" /> Сбросить
        </button>
        <button ref={checkButton} className="interactive-primary" type="button" onClick={() => setIsChecked(true)} disabled={selectedIds.length === 0}>
          Проверить порядок
        </button>
      </div>

      {isChecked && (
        <p className={`interactive-feedback ${isCorrect ? "is-correct" : "is-wrong"}`} role="status">
          {isCorrect ? <Check size={16} /> : <X size={16} />}
          {isCorrect ? explanation : incorrectExplanation}
        </p>
      )}
    </section>
  );
}
