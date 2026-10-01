import { useId, useRef, useState, type ReactNode } from "react";
import PeriodDialog from "./PeriodDialog";
import { periodById, type PeriodId } from "../insights/periods";

interface Props {
  title: string;
  period: PeriodId;
  // Added after the period label, e.g. " (4–7)".
  periodSuffix?: string;
  // Shown in a tooltip when the title is hovered, focused or tapped.
  description: string;
  // Periods this card can't show, with the reason (see PeriodDialog).
  disabledPeriods?: Partial<Record<PeriodId, string>>;
  onPeriodChange: (period: PeriodId) => void;
  // Data for the chosen period hasn't arrived yet, or failed to.
  loading?: boolean;
  error?: string | null;
  children: ReactNode;
}

export default function InsightCard({
  title,
  period,
  periodSuffix = "",
  description,
  disabledPeriods,
  onPeriodChange,
  loading = false,
  error = null,
  children,
}: Props) {
  const tooltipId = useId();
  const [dialogOpen, setDialogOpen] = useState(false);
  const periodButton = useRef<HTMLButtonElement>(null);

  const closeDialog = () => {
    setDialogOpen(false);
    periodButton.current?.focus();
  };

  return (
    // Clicking anywhere on the card opens the period dialog; keyboard users
    // get there through the period button, so the card itself isn't focusable.
    <div className="insight-card" onClick={() => setDialogOpen(true)}>
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
        <button
          ref={periodButton}
          type="button"
          className="insight-card__period"
          aria-haspopup="dialog"
          aria-label={`${title}: ${periodById(period).label}. Change period`}
          onClick={(e) => {
            e.stopPropagation();
            setDialogOpen(true);
          }}
        >
          {periodById(period).label}
          {periodSuffix}
        </button>
      </div>
      <div className="insight-card__body" aria-busy={loading}>
        {loading ? (
          <span className="insight-card__status">Loading…</span>
        ) : error ? (
          <span className="insight-card__status" title={error}>
            Couldn't load
          </span>
        ) : (
          children
        )}
      </div>
      {dialogOpen && (
        <PeriodDialog
          title={title}
          value={period}
          disabled={disabledPeriods}
          onSelect={onPeriodChange}
          onClose={closeDialog}
        />
      )}
    </div>
  );
}
