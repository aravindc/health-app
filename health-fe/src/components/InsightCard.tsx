import type { ReactNode } from "react";

interface Props {
  title: string;
  period: string;
  children: ReactNode;
}

export default function InsightCard({ title, period, children }: Props) {
  return (
    <div className="insight-card">
      <div className="insight-card__header">
        <span className="insight-card__title">{title}</span>
        <span className="insight-card__period">{period}</span>
      </div>
      <div className="insight-card__body">{children}</div>
    </div>
  );
}
