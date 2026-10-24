import { describe, it, expect } from "vitest";
import { resolveCanvasLink, safeWebUrl } from "../canvasLinks";
describe("safe canvas links", () => {
  it.each([
    "javascript:alert(1)",
    "data:text/html,x",
    "//example.com",
    "https://user:pass@example.com",
    "https://example.com/\nfoo",
    "https://example.com/\\evil",
    "https://example.com:bad",
  ])("rejects unsafe URL %s", (url) => expect(safeWebUrl(url)).toBeNull());
  it("uses native playback for HTTPS video files including signed query URLs", () => {
    expect(
      resolveCanvasLink("https://example.com/a.MP4?token=x"),
    ).toMatchObject({ mode: "direct" });
    expect(resolveCanvasLink("http://example.com/a.mp4")).toMatchObject({
      mode: "web",
    });
  });
  it.each([
    "https://youtu.be/M7lc1UVf-VE",
    "https://www.youtube.com/watch?v=M7lc1UVf-VE",
    "https://www.youtube.com/shorts/M7lc1UVf-VE",
  ])("builds only the official embed URL for %s", (url) =>
    expect(resolveCanvasLink(url)).toMatchObject({
      mode: "embed",
      embed: "https://www.youtube-nocookie.com/embed/M7lc1UVf-VE?autoplay=0",
    }),
  );
  it("preserves Bilibili episode and disables autoplay", () =>
    expect(
      resolveCanvasLink("https://www.bilibili.com/video/BV1B7411m7LV?p=2"),
    ).toMatchObject({
      mode: "embed",
      embed:
        "https://player.bilibili.com/player.html?bvid=BV1B7411m7LV&page=2&autoplay=0&danmaku=0",
    }));
  it("uses Vimeo official player", () =>
    expect(resolveCanvasLink("https://vimeo.com/76979871")).toMatchObject({
      mode: "embed",
      embed: "https://player.vimeo.com/video/76979871?autoplay=0",
    }));
  it.each([
    "https://youtube.com.evil.test/watch?v=M7lc1UVf-VE",
    "https://example.com/?iframe=https://player.vimeo.com/video/1",
    "https://b23.tv/example",
  ])("does not embed unrecognized or spoofed platforms %s", (url) =>
    expect(resolveCanvasLink(url, "video")?.mode).toBe("web"),
  );
  it("honors a saved ordinary-web choice for a video URL", () =>
    expect(resolveCanvasLink("https://youtu.be/M7lc1UVf-VE", "web")?.mode).toBe(
      "web",
    ));
});
