export type ResolvedLink = {
  url: string;
  mode: "web" | "direct" | "embed";
  embed?: string;
  provider?: string;
};
export function safeWebUrl(raw: string) {
  if (
    raw.length > 2048 ||
    [...raw].some(
      (c) =>
        c.charCodeAt(0) < 33 ||
        c.charCodeAt(0) === 127 ||
        c === String.fromCharCode(92),
    )
  )
    return null;
  try {
    const u = new URL(raw);
    if (
      !["http:", "https:"].includes(u.protocol) ||
      !u.hostname ||
      u.username ||
      u.password
    )
      return null;
    return u;
  } catch {
    return null;
  }
}
export function resolveCanvasLink(
  raw: string,
  kind: "auto" | "video" | "web" = "auto",
): ResolvedLink | null {
  const u = safeWebUrl(raw);
  if (!u) return null;
  const url = u.href;
  if (kind === "web" || u.protocol !== "https:") return { url, mode: "web" };
  const host = u.hostname.toLowerCase();
  let id = "";
  if (
    [
      "youtube.com",
      "www.youtube.com",
      "m.youtube.com",
      "youtu.be",
      "www.youtube-nocookie.com",
    ].includes(host)
  ) {
    id =
      host === "youtu.be"
        ? u.pathname.slice(1).split("/")[0]
        : u.pathname === "/watch"
          ? (u.searchParams.get("v") ?? "")
          : (/^\/(?:embed|shorts|live)\/([^/]+)/.exec(u.pathname)?.[1] ?? "");
    if (/^[\w-]{11}$/.test(id))
      return {
        url,
        mode: "embed",
        provider: "YouTube",
        embed: "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=0",
      };
  }
  if (
    [
      "bilibili.com",
      "www.bilibili.com",
      "m.bilibili.com",
      "player.bilibili.com",
    ].includes(host)
  ) {
    id =
      /\/video\/(BV[\w]{10})(?:\/|$)/.exec(u.pathname)?.[1] ??
      u.searchParams.get("bvid") ??
      "";
    if (/^BV[\w]{10}$/.test(id)) {
      const page = Number(
        u.searchParams.get("p") ?? u.searchParams.get("page") ?? 1,
      );
      return {
        url,
        mode: "embed",
        provider: "哔哩哔哩",
        embed:
          "https://player.bilibili.com/player.html?bvid=" +
          id +
          "&page=" +
          (Number.isSafeInteger(page) && page > 0 && page < 10000 ? page : 1) +
          "&autoplay=0&danmaku=0",
      };
    }
  }
  if (["vimeo.com", "www.vimeo.com", "player.vimeo.com"].includes(host)) {
    id = /^\/(?:video\/)?(\d+)(?:\/|$)/.exec(u.pathname)?.[1] ?? "";
    if (id)
      return {
        url,
        mode: "embed",
        provider: "Vimeo",
        embed: "https://player.vimeo.com/video/" + id + "?autoplay=0",
      };
  }
  if (/\.(mp4|webm|ogv)$/i.test(u.pathname)) return { url, mode: "direct" };
  return { url, mode: "web" };
}
