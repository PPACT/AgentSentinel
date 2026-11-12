import { describe, expect, it } from "vitest";

import { consumePtyStream, decodePtyFrame } from "./pty";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("decodePtyFrame", () => {
  it("decodes base64 output and ignores other events", () => {
    const chunk = decodePtyFrame("output", '{"seq":3,"data":"aGVsbG8="}');
    expect(chunk?.seq).toBe(3);
    expect(new TextDecoder().decode(chunk?.data)).toBe("hello");
    expect(decodePtyFrame("exit", "{}")).toBeNull();
    expect(decodePtyFrame("output", "not-json")).toBeNull();
  });
});

describe("consumePtyStream", () => {
  it("writes output frames and stops on exit", async () => {
    const bytes: string[] = [];
    let exit: number | null | undefined;

    await consumePtyStream(
      {
        body: streamOf([
          'event: output\ndata: {"seq":1,"data":"ZGly"}\n\n',
          'event: output\ndata: {"seq":2,"data":"DQo="}\n\n',
          'event: exit\ndata: {"exit_status":0}\n\n',
        ]),
      } as Response,
      {
        onData: (chunk) => bytes.push(new TextDecoder().decode(chunk)),
        onExit: (status) => {
          exit = status;
        },
      },
    );

    expect(bytes.join("")).toBe("dir\r\n");
    expect(exit).toBe(0);
  });
});
