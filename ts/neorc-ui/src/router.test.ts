import { describe, expect, it } from "vitest";

import { href, parseHash, type Route } from "./router";

describe("routes", () => {
  it.each<[string, Route]>([
    ["", { name: "runs", flow: undefined, status: undefined }],
    ["#/", { name: "runs", flow: undefined, status: undefined }],
    ["#/runs?flow=a&status=failed", { name: "runs", flow: "a", status: "failed" }],
    ["#/flows", { name: "flows" }],
    ["#/flows/word_picker", { name: "flow", flow: "word_picker", version: undefined }],
    ["#/flows/a%2Fb?version=1.0.0", { name: "flow", flow: "a/b", version: "1.0.0" }],
    ["#/runs/abc-123", { name: "run", id: "abc-123" }],
    ["#/nothing/here", { name: "unknown", hash: "#/nothing/here" }],
    ["#/runs/%E0", { name: "unknown", hash: "#/runs/%E0" }],
    ["#/flows/100%", { name: "unknown", hash: "#/flows/100%" }],
  ])("parses %s", (hash, route) => {
    expect(parseHash(hash)).toEqual(route);
  });

  it("writes what it reads", () => {
    const routes: Route[] = [
      { name: "runs" },
      { name: "runs", flow: "a", status: "active" },
      { name: "flows" },
      { name: "flow", flow: "a/b", version: "2.0.0" },
      { name: "run", id: "abc" },
    ];
    for (const route of routes) {
      expect(parseHash(href(route))).toMatchObject(route);
    }
  });
});
