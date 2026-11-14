import { describe, expect, it } from "vitest";

import { arrowTarget, shortcutTarget } from "./keyboard";

const TABS = [{ key: "chat" }, { key: "progress" }, { key: "terminal" }];

function event(overrides: Partial<Parameters<typeof shortcutTarget>[2]>) {
  return {
    key: "Tab",
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...overrides,
  };
}

describe("shortcutTarget", () => {
  it("cycles forward and backward with Ctrl+Tab", () => {
    expect(shortcutTarget(TABS, "chat", event({ ctrlKey: true }))?.key).toBe("progress");
    expect(shortcutTarget(TABS, "terminal", event({ ctrlKey: true }))?.key).toBe("chat");
    expect(shortcutTarget(TABS, "chat", event({ ctrlKey: true, shiftKey: true }))?.key).toBe(
      "terminal",
    );
  });

  it("also accepts Cmd+Tab", () => {
    expect(shortcutTarget(TABS, "chat", event({ metaKey: true }))?.key).toBe("progress");
  });

  it("selects tabs with Alt+1/2/3", () => {
    expect(shortcutTarget(TABS, "chat", event({ altKey: true, key: "2" }))?.key).toBe("progress");
    expect(shortcutTarget(TABS, "chat", event({ altKey: true, key: "3" }))?.key).toBe("terminal");
    expect(shortcutTarget(TABS, "chat", event({ altKey: true, key: "9" }))).toBeNull();
  });

  it("never steals plain Tab or other keys", () => {
    expect(shortcutTarget(TABS, "chat", event({}))).toBeNull();
    expect(shortcutTarget(TABS, "chat", event({ ctrlKey: true, key: "k" }))).toBeNull();
    expect(shortcutTarget(TABS, "chat", event({ altKey: true, key: "x" }))).toBeNull();
  });

  it("returns null for an empty tab list", () => {
    expect(shortcutTarget([], "chat", event({ ctrlKey: true }))).toBeNull();
  });
});

describe("arrowTarget", () => {
  it("wraps left/right and supports Home/End", () => {
    expect(arrowTarget(TABS, "chat", "ArrowRight")?.key).toBe("progress");
    expect(arrowTarget(TABS, "chat", "ArrowLeft")?.key).toBe("terminal");
    expect(arrowTarget(TABS, "progress", "Home")?.key).toBe("chat");
    expect(arrowTarget(TABS, "progress", "End")?.key).toBe("terminal");
    expect(arrowTarget(TABS, "progress", "Enter")).toBeNull();
  });
});
