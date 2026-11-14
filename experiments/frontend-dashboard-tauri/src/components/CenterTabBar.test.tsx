// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CENTER_TABS } from "@/lib/centerTabs";

import { CenterTabBar } from "./CenterTabBar";

afterEach(() => {
  cleanup();
});

function renderBar(active: "chat" | "progress" | "terminal" = "chat") {
  const onSelect = vi.fn();
  render(<CenterTabBar tabs={CENTER_TABS} active={active} onSelect={onSelect} />);
  return onSelect;
}

describe("CenterTabBar", () => {
  it("uses roving tabIndex with only the active tab in tab order", () => {
    const onSelect = renderBar("progress");

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "对话" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "进度记录" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "原生终端" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "进度记录" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches with arrow keys and moves focus to the new tab", async () => {
    const onSelect = renderBar("chat");
    const first = screen.getByRole("tab", { name: "对话" });
    first.focus();

    fireEvent.keyDown(first, { key: "ArrowRight" });

    expect(onSelect).toHaveBeenCalledWith("progress");
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("tab", { name: "进度记录" })),
    );
  });

  it("supports Home and End", () => {
    const onSelect = renderBar("progress");
    const active = screen.getByRole("tab", { name: "进度记录" });

    fireEvent.keyDown(active, { key: "Home" });
    expect(onSelect).toHaveBeenLastCalledWith("chat");

    fireEvent.keyDown(active, { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith("terminal");
  });

  it("does not intercept plain Tab or Enter", () => {
    const onSelect = renderBar("chat");
    const active = screen.getByRole("tab", { name: "对话" });

    fireEvent.keyDown(active, { key: "Tab" });
    fireEvent.keyDown(active, { key: "Enter" });

    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps focus on the tab after a mouse click", async () => {
    const onSelect = renderBar("chat");
    const target = screen.getByRole("tab", { name: "原生终端" });

    fireEvent.click(target);

    expect(onSelect).toHaveBeenCalledWith("terminal");
    await waitFor(() => expect(document.activeElement).toBe(target));
  });
});
