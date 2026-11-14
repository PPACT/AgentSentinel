import { describe, expect, it } from "vitest";

import { computeContextBreakdown, estimateTokens } from "./context";

describe("estimateTokens", () => {
  it("counts CJK per character and latin text per four characters", () => {
    expect(estimateTokens("")).toBe(0);
    expect(estimateTokens("你好")).toBe(2);
    expect(estimateTokens("abcd")).toBe(1);
    expect(estimateTokens("abcd你好")).toBe(3);
  });
});

describe("computeContextBreakdown", () => {
  it("uses the first assistant request as the system+tools base", () => {
    const result = computeContextBreakdown(
      [
        { role: "user", blocks: [{ kind: "text", text: "abcd" }] },
        {
          role: "assistant",
          blocks: [{ kind: "text", text: "ok" }],
          usage: {
            input_tokens: 1_000,
            output_tokens: 5,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
          },
        },
      ],
      1_500,
    );
    expect(result).toEqual({ systemTools: 999, messages: 500 });
  });

  it("returns null when the history has no assistant usage", () => {
    expect(
      computeContextBreakdown([{ role: "user", blocks: [{ kind: "text", text: "hi" }] }], 100),
    ).toBeNull();
  });

  it("never reports negative message growth", () => {
    const result = computeContextBreakdown(
      [
        {
          role: "assistant",
          blocks: [{ kind: "text", text: "ok" }],
          usage: {
            input_tokens: 1_200,
            output_tokens: 5,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
          },
        },
      ],
      900,
    );
    expect(result?.messages).toBe(0);
  });
});
