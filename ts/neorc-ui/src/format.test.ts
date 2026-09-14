import { describe, expect, it } from "vitest";

import { formatDuration, formatTime, formatValue, localToIso } from "./format";

describe("format", () => {
  it("shows a duration at a fitting scale, and nothing before the end", () => {
    const start = "2026-09-13T10:00:00+00:00";
    expect(formatDuration(start, "2026-09-13T10:00:00.250+00:00")).toBe("250 ms");
    expect(formatDuration(start, "2026-09-13T10:00:02.5+00:00")).toBe("2.5 s");
    expect(formatDuration(start, "2026-09-13T10:03:04+00:00")).toBe("3 min 4 s");
    expect(formatDuration(start, null)).toBe("");
    expect(formatDuration(null, start)).toBe("");
  });

  it("leaves a time it cannot read as it came, and shows none as nothing", () => {
    expect(formatTime("yesterday")).toBe("yesterday");
    expect(formatTime(null)).toBe("");
    expect(formatTime("2026-09-13T10:00:00+00:00")).not.toBe("");
  });

  it("pretty-prints a value with its datetime tags shown as dates", () => {
    const text = formatValue({ when: { $datetime: "2026-09-13T10:00:00+00:00" }, n: [1] });
    expect(text).toContain('"n": [\n    1\n  ]');
    expect(text).toContain("(2026-09-13T10:00:00+00:00)");
    expect(text).not.toContain("$datetime");
  });
});

describe("localToIso", () => {
  it("keeps the moment typed, with the browser's offset spelled out", () => {
    const iso = localToIso("2026-09-13T10:30");

    expect(iso).toMatch(/^2026-09-13T10:30:00[+-]\d\d:\d\d$/);
    expect(new Date(iso!).getTime()).toBe(new Date("2026-09-13T10:30").getTime());
  });

  it("has nothing for an empty or unreadable value", () => {
    expect(localToIso("")).toBeUndefined();
    expect(localToIso("noon")).toBeUndefined();
  });
});
