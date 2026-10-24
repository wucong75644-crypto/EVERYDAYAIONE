import type { CSSProperties } from "react";
import type { CanvasData, CanvasStyle } from "../../services/knowledge";
export const defaultStyle: CanvasStyle = {
  color: "#202938",
  background: "#ffffff",
  border: "#a8b3c5",
  font_size: 15,
  bold: false,
  italic: false,
  shape: "rounded",
  font: "system",
  underline: false,
  strike: false,
  border_width: 1,
  border_style: "solid",
};
export function nodeStyle(data?: CanvasData): CSSProperties {
  const s = data?.style;
  if (!s) return data?.width?{width:data.width,boxSizing:'border-box'}:{};
  return {
    width: data?.width??272,boxSizing:'border-box',
    color: s.color,
    background: s.background,
    borderColor: s.border,
    borderWidth: s.border_width ?? 1,
    borderStyle: s.border_style ?? "solid",
    borderRadius: s.shape === "pill" ? 40 : s.shape === "rectangle" ? 0 : 10,
  };
}
export function titleStyle(data?: CanvasData): CSSProperties {
  const s = data?.style;
  return s
    ? {
        textAlign:data?.title_format?.align??'left',
        fontFamily: {
          system: "inherit",
          sans: "Arial, Microsoft YaHei, PingFang SC, sans-serif",
          serif: "Georgia, SimSun, serif",
          mono: "Consolas, Menlo, monospace",
        }[s.font ?? "system"],
        textDecoration:
          [s.underline ? "underline" : "", s.strike ? "line-through" : ""]
            .filter(Boolean)
            .join(" ") || "none",
        fontSize: s.font_size,
        fontWeight: s.bold ? 700 : 400,
        fontStyle: s.italic ? "italic" : "normal",
      }
    : {textAlign:data?.title_format?.align??'left'};
}

export async function imageAspect(blob: Blob) {
  if (typeof createImageBitmap === "undefined") return 1;
  try {
    const image = await createImageBitmap(blob);
    const ratio = Math.max(
      0.01,
      Math.min(100, image.width / Math.max(1, image.height)),
    );
    image.close();
    return ratio;
  } catch {
    return 1;
  }
}

export function visualSizeChanged(
  before: CanvasData = {},
  after: CanvasData = {},
) {
  const metrics = (data: CanvasData) => [
    data.width??272,
    data.style?.font_size ?? 15,
    data.style?.bold ?? true,
    data.style?.italic ?? false,
    data.style?.font ?? "system",
    data.style?.border_width ?? 1,
    data.image ? data.image.width / (data.image.aspect ?? 1) : 0,
    !!data.video,!!data.link, data.title_format,
  ];
  return JSON.stringify(metrics(before)) !== JSON.stringify(metrics(after));
}
