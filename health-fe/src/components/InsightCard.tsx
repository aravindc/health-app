import { useId, type ReactNode } from "react";

interface Props {
  title: string;
  period: string;
  // Shown in a tooltip when the title is hovered, focused or tapped.
  description: string;
  children: ReactNode;
}

export default function InsightCard({ title, period, description, children }: Props) {
  const tooltipId = useId();
  return (
    <div className="insight-card">
      <div className="insight-card__header">
        <span
          className="insight-card__title"
          tabIndex={0}
          aria-describedby={tooltipId}
        >
          {title}
        </span>
        <span id={tooltipId} role="tooltip" className="insight-card__tooltip">
          {description}
        </span>
        <span className="insight-card__period">{period}</span>
      </div>
      <div className="insight-card__body">{children}</div>
    </div>
  );
}
