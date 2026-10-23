export type CanvasView = { x: number; y: number; z: number };
export type CanvasPoint = { x: number; y: number };
export const MIN_ZOOM = .02;
export function zoomAt(view: CanvasView, z: number, anchor: CanvasPoint): CanvasView {
  const next = Math.max(Math.min(MIN_ZOOM, view.z), Math.min(2, z));
  return { x: anchor.x - (anchor.x - view.x) * next / view.z, y: anchor.y - (anchor.y - view.y) * next / view.z, z: next };
}
export function wheelView(view: CanvasView, event: { deltaX: number; deltaY: number; deltaMode: number; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }, anchor: CanvasPoint, height: number): CanvasView {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
  if (event.ctrlKey || event.metaKey) return zoomAt(view, view.z * Math.exp(Math.max(-.5, Math.min(.5, -event.deltaY * unit * .003))), anchor);
  return { ...view, x: view.x - (event.shiftKey && !event.deltaX ? event.deltaY : event.deltaX) * unit, y: view.y - (event.shiftKey && !event.deltaX ? 0 : event.deltaY * unit) };
}
export function edgeVelocity(cursor: number, low: number, high: number) {
  if (cursor < low || cursor > high) return 0;
  if (cursor < low + 48) return 420 * (1 - (cursor - low) / 48);
  if (cursor > high - 48) return -420 * (1 - (high - cursor) / 48);
  return 0;
}
export function fitView(cards: { x: number; y: number; width: number; height: number }[], box: { width: number; height: number }): CanvasView | null {
  if (!cards.length || box.width < 100 || box.height < 150) return null;
  const left = Math.min(...cards.map(n => n.x)), top = Math.min(...cards.map(n => n.y));
  const width = Math.max(...cards.map(n => n.x + n.width)) - left, height = Math.max(...cards.map(n => n.y + n.height)) - top;
  const z = Math.min(2, (box.width - 80) / Math.max(width, 1), (box.height - 128) / Math.max(height, 1));
  return { x: (box.width - width * z) / 2 - left * z, y: (box.height - height * z) / 2 - top * z, z };
}
