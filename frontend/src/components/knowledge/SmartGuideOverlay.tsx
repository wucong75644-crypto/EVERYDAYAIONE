import type { SnapFeedback } from "./smartGuides";
export function SmartGuideOverlay({
  feedback,
  zoom,
}: {
  feedback: SnapFeedback;
  zoom: number;
}) {
  return (
    <svg className="kc-smart-guides" aria-hidden="true">
      {feedback.lines.map((line, i) => (
        <line key={"l" + i} {...line} vectorEffect="non-scaling-stroke" />
      ))}
      {feedback.gaps.map((gap, i) => {
        const horizontal = gap.axis === "x",
          middle = (gap.start + gap.end) / 2,
          tick = 4 / zoom;
        return (
          <g key={"g" + i}>
            <line
              x1={horizontal ? gap.start : gap.cross}
              y1={horizontal ? gap.cross : gap.start}
              x2={horizontal ? gap.end : gap.cross}
              y2={horizontal ? gap.cross : gap.end}
              vectorEffect="non-scaling-stroke"
            />
            {[gap.start, gap.end].map((value, j) => (
              <line
                key={j}
                x1={horizontal ? value : gap.cross - tick}
                x2={horizontal ? value : gap.cross + tick}
                y1={horizontal ? gap.cross - tick : value}
                y2={horizontal ? gap.cross + tick : value}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <text
              x={horizontal ? middle : gap.cross + 7 / zoom}
              y={horizontal ? gap.cross - 7 / zoom : middle}
              textAnchor={horizontal ? "middle" : "start"}
              style={{ fontSize: 12 / zoom }}
            >
              {Math.round(gap.end - gap.start)} px
            </text>
          </g>
        );
      })}
    </svg>
  );
}
