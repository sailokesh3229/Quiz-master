import type { DeliveredQuestion } from "../../lib/types";
import "./Questions.css";

interface Props {
  question: DeliveredQuestion;
  value: string | undefined;
  onChange: (value: string) => void;
}

export default function FillInBlankQuestion({ question, value, onChange }: Props) {
  return (
    <>
      <div className="q-text">{question.text}</div>
      <div className="fib-input-row">
        <input
          className="fib-input"
          type="text"
          autoFocus
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Type your answer"
        />
      </div>
      <div className="fib-hint">A close synonym or phrasing is fine — it doesn't need to be word-for-word.</div>
    </>
  );
}
