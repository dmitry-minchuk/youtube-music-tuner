import { describe, expect, it } from "vitest";
import { mergeRetunedQueue, type QueueTrack } from "@/player/playerStore";

function track(videoId: string, queueId = "q1"): QueueTrack {
  return { videoId, title: videoId, artists: [], durationSeconds: null, queueId };
}

describe("mergeRetunedQueue", () => {
  const queue = [track("a"), track("b"), track("c"), track("d")];

  it("keeps the started head including the current track", () => {
    const retuned = [track("x", "q2"), track("y", "q2")];
    const merged = mergeRetunedQueue(queue, 1, { a: 1, b: 2 }, retuned);
    expect(merged.map((item) => item.videoId)).toEqual(["a", "b", "x", "y"]);
  });

  it("replaces everything when nothing has started yet", () => {
    const retuned = [track("x", "q2")];
    const merged = mergeRetunedQueue(queue, 0, {}, retuned);
    expect(merged.map((item) => item.videoId)).toEqual(["x"]);
  });

  it("never lets a retuned track duplicate one already in the head", () => {
    const retuned = [track("b", "q2"), track("y", "q2")];
    const merged = mergeRetunedQueue(queue, 1, { a: 1, b: 2 }, retuned);
    expect(merged.map((item) => item.videoId)).toEqual(["a", "b", "y"]);
  });
});
