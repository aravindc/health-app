import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { api } from "../api/client";
import type { DataPoint, BolusDose, ActivityPoint, BasalPoint } from "../api/types";
import {
  WINDOW_HOURS,
  pxPerHour,
  xForTime,
  timeForX,
  nearest,
  parseTime,
} from "../chart/scale";

interface Props {
  minMmol?: number;
  maxMmol?: number;
  refreshTick?: number; // increment to trigger a refresh of the current window
}

// Okabe-Ito colorblind-safe categorical palette: the CGM line, food-bolus
// activity, and correction-bolus activity co-occur on/near the same panel,
// so these three need to stay visually distinct under color-vision
// deficiency. Basal is alone in its own panel and stays a plain gray.
const COLOR_CGM = "#56b4e9"; // sky blue
const COLOR_FOOD = "#e69f00"; // orange
const COLOR_CORRECTION = "#009e73"; // bluish green
const COLOR_BASAL = "#999999"; // muted gray
const COLOR_LOW = "#d55e00"; // vermillion (out-of-range shading, low)
const COLOR_HIGH = "#e69f00"; // orange (out-of-range shading, high) — reused, doesn't co-occur with food dots

const PANEL_HEIGHT_CGM = 220;
const PANEL_HEIGHT_BASAL = 130;
const PANEL_HEIGHT_ACTIVITY = 170;
const MARGIN = { top: 14, right: 10, bottom: 24, left: 44 };
const MIN_PANEL_WIDTH = 280; // guards against a 0/negative-width first measurement
const WINDOW_MS = WINDOW_HOURS * 3_600_000;
const DATA_REFETCH_DEBOUNCE_MS = 250; // wait for a drag to settle before refetching

function formatHour(epoch: number): string {
  return new Date(epoch).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTooltipHeader(epoch: number): string {
  const d = new Date(epoch);
  const date = d.toLocaleDateString([], { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `${date}, ${time}`;
}

/** "Sep 19 9:32 AM – Sep 20 9:32 AM"-style label for the current window. */
function formatWindowLabel(windowStart: number, windowEnd: number): string {
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  const start = new Date(windowStart).toLocaleString([], opts);
  const end = new Date(windowEnd).toLocaleString([], opts);
  return `${start} – ${end}`;
}

/** Linear y-scale: value -> pixel y within [0, height] (inverted, since SVG y grows downward). */
function yScale(value: number, domainMin: number, domainMax: number, height: number): number {
  const t = (value - domainMin) / (domainMax - domainMin || 1);
  return height - t * height;
}

/** Builds an SVG path `d` for a step-after area (basal): flat segments, vertical jumps at each change. */
function stepAreaPath(
  points: { x: number; y: number }[],
  endX: number,
  baselineY: number
): string {
  if (points.length === 0) return "";
  let d = `M ${points[0].x} ${baselineY} L ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    d += ` L ${points[i].x} ${points[i - 1].y} L ${points[i].x} ${points[i].y}`;
  }
  d += ` L ${endX} ${points[points.length - 1].y} L ${endX} ${baselineY} Z`;
  return d;
}

/** Builds an SVG path `d` for a smooth-ish (linear) filled area from a zero baseline. */
function areaPath(points: { x: number; y: number }[], baselineY: number): string {
  if (points.length === 0) return "";
  let d = `M ${points[0].x} ${baselineY} L ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    d += ` L ${points[i].x} ${points[i].y}`;
  }
  d += ` L ${points[points.length - 1].x} ${baselineY} Z`;
  return d;
}

function linePath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return "";
  return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
}

/** A legend chip: a small colored swatch (dot or square) plus a label. */
function LegendItem({
  color,
  label,
  shape = "dot",
}: {
  color: string;
  label: string;
  shape?: "dot" | "square";
}) {
  return (
    <span className="chart-legend__item">
      <span
        className={shape === "dot" ? "chart-legend__swatch chart-legend__swatch--dot" : "chart-legend__swatch"}
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

/**
 * One chart panel: a card with a header (title + optional subtitle, and an
 * optional legend on the right) and an SVG sized to exactly fill the
 * card's measured content width, showing the full WINDOW_HOURS window with
 * no horizontal scrolling. `onWidthChange` reports that measured width up
 * to the parent, which derives one shared px-per-hour scale from it so all
 * three panels (measured independently, but all the same content width)
 * stay pixel-aligned. Click-and-drag pans the shared window (see
 * `onDragStart`/parent's drag handling) — the panel itself only reports
 * raw pointer events, all pan/hover math lives in the parent so it can be
 * shared identically across all three panels.
 */
function ChartPanel({
  title,
  subtitle,
  legend,
  onMouseDown,
  onMouseMove,
  onMouseUp,
  onMouseLeave,
  onWidthChange,
  width,
  height,
  children,
  footer,
  dragging,
}: {
  title: string;
  subtitle?: string;
  legend?: React.ReactNode;
  onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => void;
  onMouseMove: (e: React.MouseEvent<HTMLDivElement>) => void;
  onMouseUp: (e: React.MouseEvent<HTMLDivElement>) => void;
  onMouseLeave: () => void;
  onWidthChange: (width: number) => void;
  width: number;
  height: number;
  children: React.ReactNode;
  footer?: React.ReactNode;
  dragging: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  // The SVG element itself is sized from this panel's OWN measurement,
  // never from the parent's relayed `width` prop: that prop is one shared
  // value derived from whichever panel's ResizeObserver last reported (see
  // handleWidthChange), so right after a layout shift (e.g. the insulin
  // section collapsing/expanding, or a window resize) it can lag this
  // panel's actual container width for a render or two. If the <svg>
  // width itself used that stale value, content positioned with the
  // (already-updated) shared pxPerHour scale would be laid out for a
  // wider/narrower panel than the one currently on screen and spill
  // outside the viewport. Using ownWidth for the SVG keeps the element
  // always exactly as wide as its own container; `width` is still
  // reported up so the parent can derive one shared pxPerHour for
  // cross-panel alignment.
  const [ownWidth, setOwnWidth] = useState(width);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const report = () => {
      const w = el.clientWidth;
      if (w > 0) setOwnWidth(w);
      onWidthChange(w);
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="chart-panel">
      <div className="chart-panel__header">
        <div className="chart-panel__titles">
          <span className="chart-panel__title">{title}</span>
          {subtitle && <span className="chart-panel__subtitle">{subtitle}</span>}
        </div>
        {legend && <div className="chart-legend">{legend}</div>}
      </div>

      <div
        className={dragging ? "chart-viewport chart-viewport--dragging" : "chart-viewport"}
        ref={wrapRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
      >
        <svg width={ownWidth} height={height} className="chart-svg">
          {children}
        </svg>
      </div>

      {footer && <div className="chart-panel__footer">{footer}</div>}
    </div>
  );
}

export default function GlucoseInsulinChart({
  minMmol = 4.0,
  maxMmol = 10.0,
  refreshTick = 0,
}: Props) {
  // windowStart is a continuous epoch-ms timestamp (not a whole-day
  // index): dragging any panel slides it by an arbitrary amount, so the
  // visible window can be e.g. "yesterday 11pm to today 11pm" rather than
  // always being calendar-day-aligned. Defaults to "now - 24h".
  const [windowStart, setWindowStart] = useState(() => Date.now() - WINDOW_MS);
  const [earliestStart, setEarliestStart] = useState<number | null>(null);
  const [cgmData, setCgmData] = useState<DataPoint[]>([]);
  const [doses, setDoses] = useState<BolusDose[]>([]);
  const [foodActivity, setFoodActivity] = useState<ActivityPoint[]>([]);
  const [correctionActivity, setCorrectionActivity] = useState<ActivityPoint[]>([]);
  const [basalPoints, setBasalPoints] = useState<BasalPoint[]>([]);
  const [basalWindowEnd, setBasalWindowEnd] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [collapsedInsulin, setCollapsedInsulin] = useState(false);

  // Measured content width of the panels, used to derive one shared
  // px-per-hour scale so the full 24h window exactly fills the visible
  // card with no horizontal scrolling. All three panels report their
  // width (they're all the same width by layout).
  const [panelWidth, setPanelWidth] = useState(MIN_PANEL_WIDTH);
  const handleWidthChange = useCallback((width: number) => {
    if (width <= 0) return;
    setPanelWidth((prev) => (Math.abs(prev - width) > 0.5 ? width : prev));
  }, []);
  // pxPerHour must be derived from the INNER plot width (panelWidth minus
  // the left/right margins), not the full panel width: every panel draws
  // its time-scaled content (gridlines, series, dose markers, x-axis
  // ticks) inside a `<g transform="translate(MARGIN.left, ...)">`, so the
  // actually-drawable width is panelWidth - MARGIN.left - MARGIN.right.
  // Scaling WINDOW_HOURS across the full panelWidth instead overshoots by
  // MARGIN.left + MARGIN.right px, pushing the last stretch of the window
  // past the SVG's right edge.
  const plotWidth = Math.max(0, panelWidth - MARGIN.left - MARGIN.right);
  const pxPerHr = pxPerHour(plotWidth);

  const [hoverX, setHoverX] = useState<number | null>(null); // px within the panel SVG, or null
  // Screen position of the pointer while hovering any panel, relative to
  // the outer chart container — drives one shared tooltip's position
  // (rather than a per-panel tooltip, which would need to reserve layout
  // space and cause the panels below it to jump).
  const [pointerPos, setPointerPos] = useState<{ x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // --- Drag-to-pan state ---
  // dragStartX/dragStartWindow capture the gesture's origin; dragOffsetMs
  // is the live (not-yet-committed) shift applied on top of windowStart
  // while the mouse button is held, so the chart visibly slides in real
  // time. On mouseup the offset is folded into windowStart (committing it,
  // which triggers a debounced refetch) and dragOffsetMs resets to 0.
  const draggingRef = useRef(false);
  const dragStartXRef = useRef(0);
  const dragStartWindowRef = useRef(0);
  const [dragOffsetMs, setDragOffsetMs] = useState(0);
  const [isDragging, setIsDragging] = useState(false);

  // The latest a window is allowed to start: right now minus the window
  // length, i.e. windowEnd == now (never a window that reaches into the
  // future). A stable, single definition — atLatest below compares
  // against this same value, not a freshly-recomputed one, so the two
  // never drift apart.
  const latestWindowStart = useCallback(() => Date.now() - WINDOW_MS, []);

  const clampWindowStart = useCallback(
    (start: number) => {
      let clamped = Math.min(start, latestWindowStart());
      if (earliestStart !== null) {
        clamped = Math.max(clamped, earliestStart);
      }
      return clamped;
    },
    [earliestStart, latestWindowStart]
  );

  // Fetch the earliest available data timestamp once on mount, to clamp
  // how far back panning/jumping can go.
  useEffect(() => {
    api
      .getFirstDate()
      .then(({ date }) => {
        setEarliestStart(new Date(`${date}T00:00:00`).getTime());
      })
      .catch(() => {
        /* silently ignore — panning just won't be bounded on the left */
      });
  }, []);

  const fetchWindow = useCallback(async (start: number, end: number) => {
    setLoading(true);
    const fromIso = new Date(start).toISOString();
    const toIso = new Date(end).toISOString();
    try {
      const [cgmResult, bolusResult, basalResult] = await Promise.allSettled([
        api.getRangeChart(fromIso, toIso),
        api.getBolusRangeChart(fromIso, toIso),
        api.getBasalRangeChart(fromIso, toIso),
      ]);
      setCgmData(cgmResult.status === "fulfilled" ? cgmResult.value : []);
      if (bolusResult.status === "fulfilled") {
        setDoses(bolusResult.value.doses);
        setFoodActivity(bolusResult.value.food_activity);
        setCorrectionActivity(bolusResult.value.correction_activity);
      } else {
        setDoses([]);
        setFoodActivity([]);
        setCorrectionActivity([]);
      }
      if (basalResult.status === "fulfilled") {
        setBasalPoints(basalResult.value.points);
        setBasalWindowEnd(parseTime(basalResult.value.window_end));
      } else {
        setBasalPoints([]);
        setBasalWindowEnd(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // Refetch when the (committed) window moves, or the parent triggers a
  // refresh (tick changes) — debounced so a drag gesture doesn't fire a
  // request per pixel; only the settled position after mouseup does.
  useEffect(() => {
    const id = setTimeout(() => fetchWindow(windowStart, windowStart + WINDOW_MS), DATA_REFETCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [windowStart, refreshTick, fetchWindow]);

  // --- Coarse ±24h jump buttons ---
  const goFirst = () => setWindowStart((w) => clampWindowStart(earliestStart ?? w));
  const goBack = () => setWindowStart((w) => clampWindowStart(w - WINDOW_MS));
  const goForward = () => setWindowStart((w) => clampWindowStart(w + WINDOW_MS));
  const goLatest = () => setWindowStart(() => clampWindowStart(latestWindowStart()));

  const atEarliest = earliestStart !== null && windowStart <= earliestStart;
  const atLatest = windowStart >= latestWindowStart() - 30_000; // small tolerance for time passing between renders

  // --- Drag-to-pan handlers, shared by all three panels ---
  const beginDrag = (e: React.MouseEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    dragStartXRef.current = e.clientX;
    dragStartWindowRef.current = windowStart;
    setIsDragging(true);
    setHoverX(null);
    setPointerPos(null);
  };

  const updateDrag = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    const dxPx = e.clientX - dragStartXRef.current;
    const dxMs = -(dxPx / pxPerHr) * 3_600_000; // dragging right (positive dx) reveals earlier data
    setDragOffsetMs(dxMs);
  };

  const endDrag = () => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setIsDragging(false);
    setWindowStart(clampWindowStart(dragStartWindowRef.current + dragOffsetMs));
    setDragOffsetMs(0);
  };

  // The window actually rendered: the committed windowStart, live-shifted
  // by any in-progress drag for instant visual feedback before the drag
  // is committed/refetched.
  const displayWindowStart = isDragging ? dragStartWindowRef.current + dragOffsetMs : windowStart;
  const displayWindowEnd = displayWindowStart + WINDOW_MS;

  const handlePointerMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (draggingRef.current) {
      updateDrag(e);
      return;
    }
    const container = e.currentTarget;
    const rect = container.getBoundingClientRect();
    setHoverX(e.clientX - rect.left);

    const outerRect = containerRef.current?.getBoundingClientRect();
    if (outerRect) {
      setPointerPos({ x: e.clientX - outerRect.left, y: e.clientY - outerRect.top });
    }
  };
  const handlePointerLeave = () => {
    if (!draggingRef.current) {
      setHoverX(null);
      setPointerPos(null);
    }
  };
  const handlePointerUp = () => endDrag();

  // Also end the drag if the mouse is released outside any panel.
  useEffect(() => {
    if (!isDragging) return;
    const onUp = () => endDrag();
    window.addEventListener("mouseup", onUp);
    return () => window.removeEventListener("mouseup", onUp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDragging, dragOffsetMs]);

  // hoverX is measured relative to the viewport div, which spans the full
  // panel width including MARGIN.left — the same offset the crosshair
  // lines below subtract before drawing — so it must be subtracted here
  // too, or hoverTime (and everything the tooltip looks up from it) would
  // correspond to a point MARGIN.left px to the right of the crosshair.
  const hoverTime =
    hoverX !== null && !isDragging
      ? timeForX(hoverX - MARGIN.left, displayWindowStart, pxPerHr)
      : null;

  // --- CGM panel geometry ---
  const cgmInnerHeight = PANEL_HEIGHT_CGM - MARGIN.top - MARGIN.bottom;
  const cgmDomainMin = 0;
  const cgmDomainMax = 20;
  // nearest() binary-searches assuming ascending-by-time order, but the
  // API response isn't guaranteed sorted — sort once here and reuse for
  // both the drawn line and hover lookups, rather than each recomputing
  // its own sort (or, as hoverCgm previously did, skipping it and binary
  // searching the raw unsorted array, which returns a wrong/effectively
  // static nearest-point regardless of where the pointer is).
  const sortedCgmData = useMemo(
    () => [...cgmData].sort((a, b) => a.epoch - b.epoch),
    [cgmData]
  );
  const cgmPoints = useMemo(
    () =>
      sortedCgmData.map((d) => ({
        x: xForTime(d.epoch, displayWindowStart, pxPerHr),
        y: yScale(d.mmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight),
        raw: d,
      })),
    [sortedCgmData, displayWindowStart, pxPerHr, cgmInnerHeight]
  );
  const doseMarkers = useMemo(
    () =>
      doses.map((dose) => ({
        x: xForTime(parseTime(dose.delivered_at), displayWindowStart, pxPerHr),
        dose,
      })),
    [doses, displayWindowStart, pxPerHr]
  );

  // --- Basal panel geometry ---
  const basalInnerHeight = PANEL_HEIGHT_BASAL - MARGIN.top - MARGIN.bottom;
  const maxBasalRate = Math.max(0.1, ...basalPoints.map((p) => p.commanded_rate));
  const basalStepPoints = useMemo(
    () =>
      basalPoints.map((p) => ({
        x: xForTime(parseTime(p.time), displayWindowStart, pxPerHr),
        y: yScale(p.commanded_rate, 0, maxBasalRate, basalInnerHeight),
      })),
    [basalPoints, displayWindowStart, pxPerHr, maxBasalRate, basalInnerHeight]
  );
  const basalEndTime = basalWindowEnd !== null ? basalWindowEnd : displayWindowEnd;

  // --- Bolus activity panel geometry ---
  const activityInnerHeight = PANEL_HEIGHT_ACTIVITY - MARGIN.top - MARGIN.bottom;
  const maxActivityUnits = Math.max(
    0.1,
    ...foodActivity.map((p) => p.units),
    ...correctionActivity.map((p) => p.units)
  );
  const foodAreaPoints = useMemo(
    () =>
      foodActivity.map((p) => ({
        x: xForTime(parseTime(p.time), displayWindowStart, pxPerHr),
        y: yScale(p.units, 0, maxActivityUnits, activityInnerHeight),
      })),
    [foodActivity, displayWindowStart, pxPerHr, maxActivityUnits, activityInnerHeight]
  );
  const correctionAreaPoints = useMemo(
    () =>
      correctionActivity.map((p) => ({
        x: xForTime(parseTime(p.time), displayWindowStart, pxPerHr),
        y: yScale(p.units, 0, maxActivityUnits, activityInnerHeight),
      })),
    [correctionActivity, displayWindowStart, pxPerHr, maxActivityUnits, activityInnerHeight]
  );

  // Hour gridlines/ticks across the full window.
  const hourTicks = Array.from({ length: WINDOW_HOURS + 1 }, (_, i) => i);
  const windowLabelText = formatWindowLabel(displayWindowStart, displayWindowEnd);

  // --- One shared tooltip's data, combining all three panels' series at
  // the hovered timestamp. Kept as a single overlay (rather than one
  // tooltip per panel) so it never reserves layout space in any panel and
  // can't push the panels below it around. Suppressed while dragging. ---
  const hoverCgm = hoverTime !== null ? nearest(sortedCgmData, hoverTime, (d) => d.epoch) : null;
  const hoverBasal =
    hoverTime !== null
      ? [...basalPoints].reverse().find((p) => parseTime(p.time) <= hoverTime!)
      : null;
  const hoverFood =
    hoverTime !== null ? nearest(foodActivity, hoverTime, (p) => parseTime(p.time)) : null;
  const hoverCorrection =
    hoverTime !== null
      ? nearest(correctionActivity, hoverTime, (p) => parseTime(p.time))
      : null;

  return (
    <div className="glucose-insulin-chart" ref={containerRef}>
      <div className="bg-chart__header">
        <button className="bg-chart__nav" onClick={goFirst} disabled={atEarliest} title="Earliest available data">
          «
        </button>
        <button className="bg-chart__nav" onClick={goBack} disabled={atEarliest} title="Back 24h">
          ‹
        </button>
        <h2 className="bg-chart__title">
          {windowLabelText}
          {loading && <span className="bg-chart__loading"> …</span>}
        </h2>
        <button className="bg-chart__nav" onClick={goForward} disabled={atLatest} title="Forward 24h">
          ›
        </button>
        <button className="bg-chart__nav" onClick={goLatest} disabled={atLatest} title="Latest (now)">
          »
        </button>
      </div>
      <div className="chart-pan-hint">Scroll or drag any panel to move through the full range · charts stay in sync</div>

      {/* ===== Sensor glucose panel ===== */}
      <ChartPanel
        title="Sensor glucose"
        subtitle="mmol/L"
        onMouseDown={beginDrag}
        onMouseMove={handlePointerMove}
        onMouseUp={handlePointerUp}
        onMouseLeave={handlePointerLeave}
        onWidthChange={handleWidthChange}
        width={panelWidth}
        height={PANEL_HEIGHT_CGM}
        dragging={isDragging}
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* out-of-range shading */}
          <rect
            x={0}
            y={yScale(cgmDomainMax, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            width={panelWidth - MARGIN.left - MARGIN.right}
            height={yScale(maxMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            fill={COLOR_HIGH}
            opacity={0.1}
          />
          <rect
            x={0}
            y={yScale(minMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            width={panelWidth - MARGIN.left - MARGIN.right}
            height={cgmInnerHeight - yScale(minMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            fill={COLOR_LOW}
            opacity={0.1}
          />

          {/* target range dashed outline (not a filled band — keeps the line visible where it crosses) */}
          <line
            x1={0}
            x2={panelWidth - MARGIN.left - MARGIN.right}
            y1={yScale(minMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            y2={yScale(minMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            stroke={COLOR_LOW}
            strokeDasharray="4 4"
            strokeWidth={1.25}
          />
          <line
            x1={0}
            x2={panelWidth - MARGIN.left - MARGIN.right}
            y1={yScale(maxMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            y2={yScale(maxMmol, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
            stroke={COLOR_HIGH}
            strokeDasharray="4 4"
            strokeWidth={1.25}
          />

          {/* faint hour gridlines */}
          {hourTicks
            .filter((h) => h % 3 === 0)
            .map((h) => (
              <line
                key={`cgm-grid-${h}`}
                x1={h * pxPerHr}
                x2={h * pxPerHr}
                y1={0}
                y2={cgmInnerHeight}
                stroke="#fff"
                strokeWidth={1}
                opacity={0.04}
              />
            ))}

          {/* dashed vertical lines at each dose timestamp, threaded through full panel height */}
          {doseMarkers.map(({ x, dose }) => (
            <line
              key={`cgm-dose-line-${dose.bolus_id}`}
              x1={x}
              x2={x}
              y1={0}
              y2={cgmInnerHeight}
              stroke={dose.dominant_category === "food" ? COLOR_FOOD : COLOR_CORRECTION}
              strokeDasharray="2 3"
              strokeWidth={1}
              opacity={0.4}
            />
          ))}

          {/* CGM line */}
          <path d={linePath(cgmPoints)} fill="none" stroke={COLOR_CGM} strokeWidth={1.75} />

          {/* dose dots along the top of the panel */}
          {doseMarkers.map(({ x, dose }) => (
            <circle
              key={`cgm-dose-dot-${dose.bolus_id}`}
              cx={x}
              cy={2}
              r={4}
              fill={dose.dominant_category === "food" ? COLOR_FOOD : COLOR_CORRECTION}
            />
          ))}

          {/* crosshair */}
          {hoverX !== null && !isDragging && (
            <line
              x1={hoverX - MARGIN.left}
              x2={hoverX - MARGIN.left}
              y1={0}
              y2={cgmInnerHeight}
              stroke="#fff"
              strokeWidth={1}
              opacity={0.5}
            />
          )}

          {/* y-axis labels */}
          {[minMmol, maxMmol].map((v) => (
            <text
              key={`cgm-y-${v}`}
              x={-8}
              y={yScale(v, cgmDomainMin, cgmDomainMax, cgmInnerHeight)}
              fill="#888"
              fontSize={11}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {v.toFixed(1)}
            </text>
          ))}

          {/* x-axis hour ticks */}
          {hourTicks
            .filter((h) => h % 3 === 0)
            .map((h) => (
              <text
                key={`cgm-x-${h}`}
                x={h * pxPerHr}
                y={cgmInnerHeight + 16}
                fill="#888"
                fontSize={10}
                textAnchor="middle"
              >
                {formatHour(displayWindowStart + h * 3_600_000)}
              </text>
            ))}
        </g>
      </ChartPanel>

      <button
        className="chart-collapse-toggle"
        onClick={() => setCollapsedInsulin((c) => !c)}
        aria-expanded={!collapsedInsulin}
      >
        {collapsedInsulin ? "▸ Show insulin delivery" : "▾ Hide insulin delivery"}
      </button>

      {!collapsedInsulin && (
        <>
          {/* ===== Basal delivery panel ===== */}
          <ChartPanel
            title="Basal delivery"
            subtitle="U/hr, commanded rate"
            legend={<LegendItem color={COLOR_BASAL} label="Basal rate" shape="square" />}
            onMouseDown={beginDrag}
            onMouseMove={handlePointerMove}
            onMouseUp={handlePointerUp}
            onMouseLeave={handlePointerLeave}
            onWidthChange={handleWidthChange}
            width={panelWidth}
            height={PANEL_HEIGHT_BASAL}
            dragging={isDragging}
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {hourTicks
                .filter((h) => h % 3 === 0)
                .map((h) => (
                  <line
                    key={`basal-grid-${h}`}
                    x1={h * pxPerHr}
                    x2={h * pxPerHr}
                    y1={0}
                    y2={basalInnerHeight}
                    stroke="#fff"
                    strokeWidth={1}
                    opacity={0.04}
                  />
                ))}

              <path
                d={stepAreaPath(
                  basalStepPoints,
                  xForTime(basalEndTime, displayWindowStart, pxPerHr),
                  basalInnerHeight
                )}
                fill={COLOR_BASAL}
                fillOpacity={0.8}
                stroke={COLOR_BASAL}
                strokeWidth={1.25}
              />

              {hoverX !== null && !isDragging && (
                <line
                  x1={hoverX - MARGIN.left}
                  x2={hoverX - MARGIN.left}
                  y1={0}
                  y2={basalInnerHeight}
                  stroke="#fff"
                  strokeWidth={1}
                  opacity={0.5}
                />
              )}

              <text x={-8} y={basalInnerHeight} fill="#888" fontSize={11} textAnchor="end">
                0
              </text>
              <text x={-8} y={8} fill="#888" fontSize={11} textAnchor="end">
                {maxBasalRate.toFixed(2)}
              </text>

              {hourTicks
                .filter((h) => h % 3 === 0)
                .map((h) => (
                  <text
                    key={`basal-x-${h}`}
                    x={h * pxPerHr}
                    y={basalInnerHeight + 16}
                    fill="#888"
                    fontSize={10}
                    textAnchor="middle"
                  >
                    {formatHour(displayWindowStart + h * 3_600_000)}
                  </text>
                ))}
            </g>
          </ChartPanel>

          {/* ===== Bolus insulin activity panel ===== */}
          <ChartPanel
            title="Bolus insulin activity"
            subtitle="U active per 5-min interval"
            legend={
              <>
                <LegendItem color={COLOR_FOOD} label="Food bolus" />
                <LegendItem color={COLOR_CORRECTION} label="Correction bolus" />
              </>
            }
            onMouseDown={beginDrag}
            onMouseMove={handlePointerMove}
            onMouseUp={handlePointerUp}
            onMouseLeave={handlePointerLeave}
            onWidthChange={handleWidthChange}
            width={panelWidth}
            height={PANEL_HEIGHT_ACTIVITY}
            dragging={isDragging}
            footer={
              <>
                Each delivered unit is spread over the 4 hours following the dose using a
                symmetric bell-shaped weight <strong>sin²(πh/4)</strong> (h = hours since dose),
                which rises from zero at the moment of the dose, peaks at h = 2, and returns to
                exactly zero at h = 4 — a standard shape for modeling rapid-acting insulin
                action, not a per-patient pharmacokinetic measurement. Food and correction are
                drawn as separate, overlapping curves rather than stacked, so each dose's own
                bell is visible on its own terms; doses taken less than 4 hours apart sum
                together into a wider or multi-peaked shape — that's the modeled activity
                actually overlapping, not a rendering artifact.
              </>
            }
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {hourTicks
                .filter((h) => h % 3 === 0)
                .map((h) => (
                  <line
                    key={`activity-grid-${h}`}
                    x1={h * pxPerHr}
                    x2={h * pxPerHr}
                    y1={0}
                    y2={activityInnerHeight}
                    stroke="#fff"
                    strokeWidth={1}
                    opacity={0.04}
                  />
                ))}

              {doseMarkers.map(({ x, dose }) => (
                <line
                  key={`activity-dose-line-${dose.bolus_id}`}
                  x1={x}
                  x2={x}
                  y1={0}
                  y2={activityInnerHeight}
                  stroke={dose.dominant_category === "food" ? COLOR_FOOD : COLOR_CORRECTION}
                  strokeDasharray="2 3"
                  strokeWidth={1}
                  opacity={0.4}
                />
              ))}

              {/* un-stacked overlapping areas, both from the same zero baseline.
                  fillOpacity (not opacity) is used so the outline stroke stays
                  fully saturated instead of also being washed out. */}
              <path
                d={areaPath(foodAreaPoints, activityInnerHeight)}
                fill={COLOR_FOOD}
                fillOpacity={0.8}
                stroke={COLOR_FOOD}
                strokeWidth={1.5}
              />
              <path
                d={areaPath(correctionAreaPoints, activityInnerHeight)}
                fill={COLOR_CORRECTION}
                fillOpacity={0.8}
                stroke={COLOR_CORRECTION}
                strokeWidth={1.5}
              />

              {hoverX !== null && !isDragging && (
                <line
                  x1={hoverX - MARGIN.left}
                  x2={hoverX - MARGIN.left}
                  y1={0}
                  y2={activityInnerHeight}
                  stroke="#fff"
                  strokeWidth={1}
                  opacity={0.5}
                />
              )}

              <text x={-8} y={activityInnerHeight} fill="#888" fontSize={11} textAnchor="end">
                0
              </text>
              <text x={-8} y={8} fill="#888" fontSize={11} textAnchor="end">
                {maxActivityUnits.toFixed(2)}
              </text>

              {hourTicks
                .filter((h) => h % 3 === 0)
                .map((h) => (
                  <text
                    key={`activity-x-${h}`}
                    x={h * pxPerHr}
                    y={activityInnerHeight + 16}
                    fill="#888"
                    fontSize={10}
                    textAnchor="middle"
                  >
                    {formatHour(displayWindowStart + h * 3_600_000)}
                  </text>
                ))}
            </g>
          </ChartPanel>
        </>
      )}

      {/* One shared tooltip for all three panels, absolutely positioned
          near the pointer so it never reserves layout space in any panel
          (a per-panel tooltip would push the panels below it around as it
          mounts/resizes on hover). Hidden while dragging. */}
      {!isDragging &&
        hoverTime !== null &&
        pointerPos &&
        (hoverCgm || hoverBasal || hoverFood || hoverCorrection) && (
          <div
            className="chart-combined-tooltip"
            style={{
              left: Math.min(pointerPos.x + 14, panelWidth - 260),
              top: pointerPos.y + 14,
            }}
          >
            <div className="chart-combined-tooltip__header">{formatTooltipHeader(hoverTime)}</div>
            <table className="chart-combined-tooltip__table">
              <tbody>
                {hoverCgm && (
                  <tr>
                    <td>
                      <span className="chart-legend__swatch" style={{ background: COLOR_CGM }} />
                      CGM
                    </td>
                    <td className="chart-combined-tooltip__value">{hoverCgm.mmol.toFixed(1)} mmol/L</td>
                  </tr>
                )}
                {hoverBasal && (
                  <tr>
                    <td>
                      <span className="chart-legend__swatch" style={{ background: COLOR_BASAL }} />
                      Basal
                    </td>
                    <td className="chart-combined-tooltip__value">
                      {hoverBasal.commanded_rate.toFixed(2)} U/hr
                    </td>
                  </tr>
                )}
                {hoverFood && (
                  <tr>
                    <td>
                      <span className="chart-legend__swatch" style={{ background: COLOR_FOOD }} />
                      Food bolus activity
                    </td>
                    <td className="chart-combined-tooltip__value">{hoverFood.units.toFixed(3)} U</td>
                  </tr>
                )}
                {hoverCorrection && (
                  <tr>
                    <td>
                      <span className="chart-legend__swatch" style={{ background: COLOR_CORRECTION }} />
                      Correction activity
                    </td>
                    <td className="chart-combined-tooltip__value">
                      {hoverCorrection.units.toFixed(3)} U
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
