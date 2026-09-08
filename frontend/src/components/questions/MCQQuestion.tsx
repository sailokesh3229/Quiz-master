import type { DeliveredQuestion, MCQPublicPayload } from "../../lib/types";
import "./Questions.css";

const LETTERS = ["A", "B", "C", "D"];

interface Props {
  question: DeliveredQuestion;
  value: number | undefined;
  onChange: (value: number) => void;
}

export default function MCQQuestion({ question, value, onChange }: Props) {
  const payload = question.payload as MCQPublicPayload;
  return (
    <>
      <div className="q-text">{question.text}</div>
      <div className="mcq-options">
        {payload.options.map((option, i) => (
          <button key={i} className={`mcq-option ${value === i ? "selected" : ""}`} onClick={() => onChange(i)}>
            <span className="mcq-letter">{LETTERS[i]}</span>
            <span>{option}</span>
          </button>
        ))}
      </div>
    </>
  );
}
