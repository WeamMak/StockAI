import { formatDate, formatNumber } from "../presentation";

interface InventoryPoint {
  projection_date: string;
  quantity: string;
}

interface InventoryChartProps {
  reorderMinimum: string;
  timeline: InventoryPoint[];
}

const width = 640;
const height = 230;
const padding = { bottom: 34, left: 38, right: 18, top: 18 };

export function InventoryChart({
  reorderMinimum,
  timeline,
}: InventoryChartProps) {
  if (timeline.length === 0) {
    return <p className="empty-inline">No inventory projection is available.</p>;
  }
  const quantities = timeline.map((point) => Number(point.quantity));
  const threshold = Number(reorderMinimum);
  const maxValue = Math.max(
    1,
    Number.isFinite(threshold) ? threshold : 0,
    ...quantities.map((value) => (Number.isFinite(value) ? value : 0)),
  );
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const x = (index: number) =>
    padding.left + (index / Math.max(1, timeline.length - 1)) * chartWidth;
  const y = (value: number) =>
    padding.top + chartHeight - (Math.max(0, value) / maxValue) * chartHeight;
  const points = quantities
    .map((value, index) => `${x(index)},${y(Number.isFinite(value) ? value : 0)}`)
    .join(" ");
  const thresholdY = y(Number.isFinite(threshold) ? threshold : 0);
  const start = formatDate(timeline[0].projection_date);
  const end = formatDate(timeline[timeline.length - 1].projection_date);

  return (
    <div className="inventory-chart">
      <svg
        aria-label={`Inventory projection from ${start} to ${end}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <line
          className="chart-grid"
          x1={padding.left}
          x2={width - padding.right}
          y1={padding.top}
          y2={padding.top}
        />
        <line
          className="chart-grid"
          x1={padding.left}
          x2={width - padding.right}
          y1={padding.top + chartHeight / 2}
          y2={padding.top + chartHeight / 2}
        />
        <line
          className="chart-axis"
          x1={padding.left}
          x2={width - padding.right}
          y1={padding.top + chartHeight}
          y2={padding.top + chartHeight}
        />
        <line
          className="chart-threshold"
          x1={padding.left}
          x2={width - padding.right}
          y1={thresholdY}
          y2={thresholdY}
        />
        <polyline className="chart-line" points={points} />
        {quantities.map((value, index) => (
          <circle
            className="chart-point"
            cx={x(index)}
            cy={y(Number.isFinite(value) ? value : 0)}
            key={timeline[index].projection_date}
            r="2.6"
          />
        ))}
        <text className="chart-label" x={padding.left} y={height - 8}>
          {formatDate(timeline[0].projection_date)}
        </text>
        <text
          className="chart-label chart-label--end"
          x={width - padding.right}
          y={height - 8}
        >
          {formatDate(timeline[timeline.length - 1].projection_date)}
        </text>
        <text className="chart-label" x="4" y={padding.top + 4}>
          {formatNumber(String(maxValue))}
        </text>
        <text className="chart-label" x="18" y={padding.top + chartHeight + 4}>
          0
        </text>
      </svg>
      <div className="chart-legend" aria-hidden="true">
        <span><i className="legend-line legend-line--projection" />Projected inventory</span>
        <span><i className="legend-line legend-line--threshold" />Reorder minimum {formatNumber(reorderMinimum)}</span>
      </div>
    </div>
  );
}
