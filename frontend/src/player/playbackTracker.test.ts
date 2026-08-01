import { describe, expect, it } from "vitest";
import {
  PlaybackTracker,
  PROGRESS_TICK_SECONDS,
  type Sample,
} from "@/player/playbackTracker";
import type { PlayerState } from "@/player/types";

function sample(
  monotonicMs: number,
  position: number,
  state: PlayerState = "PLAYING",
  duration: number | null = 200,
): Sample {
  return { monotonicMs, position, state, duration };
}

/** Feed n one-second playing samples starting from the given offsets. */
function playSeconds(tracker: PlaybackTracker, count: number, startMs = 0, startPos = 0): void {
  for (let index = 0; index <= count; index += 1) {
    tracker.observe(sample(startMs + index * 1000, startPos + index));
  }
}

describe("PlaybackTracker", () => {
  it("counts a plausible playing interval", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 10);
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(10, 3);
  });

  it("does not count a forward seek as listening", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 5);
    // Position jumps 100s ahead within one second.
    tracker.observe(sample(6000, 105));
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(5, 3);
    expect(tracker.snapshot.seekForwardSeconds).toBeGreaterThan(90);
  });

  it("reports a forward seek observation once", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 2);
    const seek = tracker.observe(sample(3000, 90));
    expect(seek).toEqual({ direction: "forward", seconds: expect.any(Number) });
    expect(tracker.snapshot.seekForwardCount).toBe(1);
  });

  it("records a backward seek without inflating played time", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 30);
    const seek = tracker.observe(sample(31_000, 5));
    expect(seek?.direction).toBe("backward");
    expect(tracker.snapshot.seekBackwardCount).toBe(1);
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(30, 3);
  });

  it("does not count buffering as playback", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 5);
    tracker.observe(sample(6000, 5, "BUFFERING"));
    tracker.observe(sample(7000, 5, "BUFFERING"));
    tracker.observe(sample(8000, 5, "PLAYING"));
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(5, 3);
    expect(tracker.snapshot.bufferedSeconds).toBeGreaterThan(0);
  });

  it("does not count time while paused", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 5);
    tracker.observe(sample(6000, 5, "PAUSED"));
    tracker.observe(sample(7000, 5, "PAUSED"));
    tracker.observe(sample(8000, 5, "PLAYING"));
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(5, 3);
  });

  it("ignores an implausible clock jump, such as a frozen tab", () => {
    const tracker = new PlaybackTracker();
    tracker.observe(sample(0, 0));
    // Ten seconds of wall clock in one step: the tab was suspended.
    tracker.observe(sample(10_000, 10));
    expect(tracker.snapshot.playedSeconds).toBe(0);
  });

  it("prefers the player duration over catalogue metadata", () => {
    const tracker = new PlaybackTracker(300);
    expect(tracker.snapshot.durationSource).toBe("METADATA");
    tracker.observe(sample(0, 0, "PLAYING", 187));
    expect(tracker.snapshot.durationSource).toBe("PLAYER");
    expect(tracker.snapshot.effectiveDurationSeconds).toBe(187);
  });

  it("leaves the duration unknown when nothing reports one", () => {
    const tracker = new PlaybackTracker();
    tracker.observe(sample(0, 0, "PLAYING", null));
    expect(tracker.snapshot.durationSource).toBe("UNKNOWN");
    expect(tracker.snapshot.effectiveDurationSeconds).toBeNull();
  });

  it("emits a progress tick every 15 played seconds", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, PROGRESS_TICK_SECONDS - 2);
    expect(tracker.shouldEmitTick()).toBe(false);

    playSeconds(tracker, 3, (PROGRESS_TICK_SECONDS - 2) * 1000, PROGRESS_TICK_SECONDS - 2);
    expect(tracker.shouldEmitTick()).toBe(true);

    tracker.markTickEmitted();
    expect(tracker.shouldEmitTick()).toBe(false);
  });

  it("ticks are driven by played time, not wall clock", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 5);
    // Twenty seconds paused must not trigger a tick.
    for (let index = 0; index < 20; index += 1) {
      tracker.observe(sample(6000 + index * 1000, 5, "PAUSED"));
    }
    expect(tracker.shouldEmitTick()).toBe(false);
  });

  it("produces a cumulative payload", () => {
    const tracker = new PlaybackTracker();
    playSeconds(tracker, 12);
    const payload = tracker.payload();
    expect(payload.playedSeconds).toBeCloseTo(12, 3);
    expect(payload.durationSource).toBe("PLAYER");
    expect(payload.effectiveDurationSeconds).toBe(200);
  });

  it("tracks wall clock separately from played time", () => {
    const tracker = new PlaybackTracker();
    tracker.observe(sample(0, 0));
    tracker.observe(sample(1000, 1));
    tracker.observe(sample(2000, 1, "PAUSED"));
    tracker.observe(sample(60_000, 1, "PAUSED"));
    expect(tracker.snapshot.playedSeconds).toBeCloseTo(1, 3);
    expect(tracker.snapshot.wallClockSeconds).toBeCloseTo(60, 3);
  });
});
