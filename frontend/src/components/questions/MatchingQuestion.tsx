import { DndContext, type DragEndEvent, useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import type { DeliveredQuestion, MatchingAnswer, MatchingPublicPayload } from "../../lib/types";
import "./Questions.css";

interface Props {
  question: DeliveredQuestion;
  value: MatchingAnswer | undefined;
  onChange: (value: MatchingAnswer) => void;
}

function DraggableChip({ id, label }: { id: string; label: string }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id });
  const style = { transform: CSS.Translate.toString(transform) };
  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={`matching-chip ${isDragging ? "dragging" : ""}`}
    >
      {label}
    </div>
  );
}

function DroppableSlot({
  leftItem,
  filled,
  onRemove,
}: {
  leftItem: string;
  filled: string | undefined;
  onRemove: () => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: leftItem });
  return (
    <div
      ref={setNodeRef}
      className={`matching-slot ${filled ? "filled" : "empty"} ${isOver ? "drag-over" : ""}`}
      onClick={filled ? onRemove : undefined}
      title={filled ? "Click to remove" : undefined}
    >
      {filled ?? "Drop here"}
    </div>
  );
}

export default function MatchingQuestion({ question, value, onChange }: Props) {
  const payload = question.payload as MatchingPublicPayload;
  const placed = value ?? {};
  const placedValues = new Set(Object.values(placed));
  const pool = payload.right_items.filter((r) => !placedValues.has(r));

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;
    const leftItem = String(over.id);
    const rightItem = String(active.id);
    const next = { ...placed };
    for (const key of Object.keys(next)) {
      if (next[key] === rightItem) delete next[key];
    }
    next[leftItem] = rightItem;
    onChange(next);
  }

  return (
    <DndContext onDragEnd={handleDragEnd}>
      <div className="q-text" style={{ fontSize: 22, marginBottom: 18 }}>
        {question.text}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
        {payload.left_items.map((left) => (
          <div className="matching-row" key={left}>
            <span className="matching-left">{left}</span>
            <DroppableSlot
              leftItem={left}
              filled={placed[left]}
              onRemove={() => {
                const next = { ...placed };
                delete next[left];
                onChange(next);
              }}
            />
          </div>
        ))}
      </div>
      <div className="matching-pool-label">DRAG FROM HERE</div>
      <div className="matching-pool">
        {pool.map((item) => (
          <DraggableChip key={item} id={item} label={item} />
        ))}
      </div>
      <div style={{ marginTop: 16 }}>
        <button className="matching-reset" onClick={() => onChange({})}>
          Reset pairs
        </button>
      </div>
    </DndContext>
  );
}
