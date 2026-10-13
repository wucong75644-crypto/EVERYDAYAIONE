import { it, expect } from "vitest";
it("loads the real publishing modules without circular initialization errors", async () => {
  const calendar = await import("../PublishCalendar");
  const studio = await import("../CardStudio");
  expect(typeof calendar.default).toBe("function");
  expect(typeof studio.SocialAssetPreview).toBe("function");
});
