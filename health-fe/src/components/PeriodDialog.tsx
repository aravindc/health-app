import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { PERIODS, type PeriodId } from "../insights/periods";

interface Props {
  title: string; // the card's title, e.g. "Median"
  value: PeriodId;
  // Periods the card can't show, with the reason given beside them.
  disabled?: Partial<Record<PeriodId, string>>;
  onSelect: (period: PeriodId) => void;
  onClose: () => void;
}

/**
 * Modal for picking an Insights card's period. Focus starts on the current
 * period, Tab stays inside, and Escape or a click on the backdrop closes it.
 * Rendered into <body> so no card's stacking context can clip it.
 */
export default function PeriodDialog({ title, value, disabled = {}, onSelect, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const headingId = `period-dialog-${title.replace(/\W+/g, "-").toLowerCase()}`;

  useEffect(() => {
    dialogRef.current
      ?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')
      ?.focus();
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const buttons = [
      ...dialogRef.current!.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
    ];
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.shiftKey
      ? (i <= 0 ? buttons.length : i) - 1
      : (i + 1) % buttons.length;
    e.preventDefault();
    buttons[next]?.focus();
  };

  return createPortal(
    // Events from a portal still bubble through the React tree to the card,
    // whose click opens this dialog, so stop them here.
    <div
      className="period-dialog__backdrop"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="period-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <h3 id={headingId} className="period-dialog__title">
          {title}
        </h3>
        <div className="period-dialog__options">
          {PERIODS.map((p) => {
            const reason = disabled[p.id];
            return (
              <button
                key={p.id}
                type="button"
                className="period-dialog__option"
                aria-pressed={p.id === value}
                disabled={reason !== undefined}
                title={reason}
                onClick={() => {
                  onSelect(p.id);
                  onClose();
                }}
              >
                {p.short}
              </button>
            );
          })}
        </div>
        {Object.values(disabled).length > 0 && (
          <p className="period-dialog__note">{[...new Set(Object.values(disabled))].join(" ")}</p>
        )}
      </div>
    </div>,
    document.body
  );
}
