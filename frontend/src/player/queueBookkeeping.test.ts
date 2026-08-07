/**
 * Queue bookkeeping (docs/04 section 8): starting a track marks it played on
 * its own wave, and the queue asks for a local extension three tracks before
 * the end.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlayerPort } from "@/player/types";

const posted = vi.hoisted(() => ({ calls: [] as { path: string; body: unknown }[] }));
const patched = vi.hoisted(() => ({ calls: [] as { path: string; body: unknown }[] }));

function waveResponse(queueId: string, videoIds: string[]) {
  return {
    queueId,
    generationId: `gen-${queueId}`,
    mix: { targetFamiliarPercent: 15, actualFamiliarPercent: 15, actualDiscoveryPercent: 85 },
    relaxations: [],
    items: videoIds.map((videoId, index) => ({
      position: index + 1,
      track: { videoId, title: videoId, artists: [] },
      reasonCodes: [],
      familiarity: "DISCOVERY",
    })),
  };
}

vi.mock("@/api/client", () => ({
  api: {
    post: (path: string, body?: unknown) => {
      posted.calls.push({ path, body });
      if (path.endsWith("/extend")) {
        return Promise.resolve(waveResponse("q-ext", ["fresh-1", "b"]));
      }
      return Promise.resolve({});
    },
    get: vi.fn(),
    put: vi.fn(),
    patch: (path: string, body?: unknown) => {
      patched.calls.push({ path, body });
      return Promise.resolve(waveResponse("q-adapted", ["adapted-1", "adapted-2"]));
    },
    delete: vi.fn(),
  },
}));

vi.mock("@/player/telemetryClient", () => ({
  TelemetryClient: class {
    record = () => Promise.resolve();
    flush = () => Promise.resolve();
    flushWithBeacon = () => undefined;
    pendingCount = () => Promise.resolve(0);
  },
}));

import { ADAPT_DEBOUNCE_MS, usePlayerStore, type QueueTrack } from "@/player/playerStore";

function fakePort(): PlayerPort {
  return {
    cue: () => Promise.resolve(),
    play: () => undefined,
    pause: () => undefined,
    seekTo: () => undefined,
    setVolume: () => undefined,
    currentTime: () => 0,
    duration: () => 0,
    state: () => "PAUSED",
    subscribe: () => () => undefined,
  } as unknown as PlayerPort;
}

function track(videoId: string): QueueTrack {
  return { videoId, title: videoId, artists: [], durationSeconds: null, queueId: "q1" };
}

const meta = {
  queueId: "q1",
  generationId: "g1",
  targetFamiliarPercent: 15,
  actualFamiliarPercent: 15,
  relaxations: [],
  temperature: 100,
  mood: "ANY",
};

async function flushMicrotasks() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("queue bookkeeping", () => {
  beforeEach(() => {
    posted.calls = [];
    usePlayerStore.setState({
      port: fakePort(),
      queue: [],
      index: 0,
      sessionId: null,
      waveMeta: null,
      lastPlayedAt: {},
    });
  });

  it("marks a started track as played on the wave that delivered it", async () => {
    usePlayerStore.setState({
      queue: [track("a"), track("b"), track("c"), track("d"), track("e"), track("f")],
      waveMeta: meta,
    });
    await usePlayerStore.getState().playIndex(0);
    await flushMicrotasks();

    const played = posted.calls.filter((call) => call.path.includes("/played"));
    expect(played).toHaveLength(1);
    expect(played[0]?.path).toBe("/api/v1/waves/q1/played");
    expect(played[0]?.body).toEqual({ videoId: "a" });
  });

  it("extends the queue three tracks before the end and deduplicates", async () => {
    usePlayerStore.setState({
      queue: [track("a"), track("b"), track("c"), track("d"), track("e")],
      waveMeta: meta,
    });
    // index 2 of 5: exactly two tracks remain after this one -> extend.
    await usePlayerStore.getState().playIndex(2);
    await flushMicrotasks();

    expect(posted.calls.some((call) => call.path === "/api/v1/waves/q1/extend")).toBe(true);
    const queue = usePlayerStore.getState().queue.map((item) => item.videoId);
    expect(queue).toEqual(["a", "b", "c", "d", "e", "fresh-1"]);
    // The appended item remembers the wave that delivered it.
    expect(usePlayerStore.getState().queue.at(-1)?.queueId).toBe("q-ext");
  });

  it("does not extend while the end is still far away", async () => {
    usePlayerStore.setState({
      queue: Array.from({ length: 10 }, (_, i) => track(`t${i}`)),
      waveMeta: meta,
    });
    await usePlayerStore.getState().playIndex(0);
    await flushMicrotasks();

    expect(posted.calls.some((call) => call.path.endsWith("/extend"))).toBe(false);
  });

  it("veto skips ahead to the next track", async () => {
    usePlayerStore.setState({
      queue: Array.from({ length: 10 }, (_, i) => track(`t${i}`)),
      waveMeta: meta,
    });
    await usePlayerStore.getState().playIndex(0);
    await flushMicrotasks();

    await usePlayerStore.getState().vetoCurrent();
    await flushMicrotasks();

    expect(usePlayerStore.getState().index).toBe(1);
  });
});

describe("adaptive tail retune (docs/04 s.8)", () => {
  beforeEach(() => {
    posted.calls = [];
    patched.calls = [];
    vi.useFakeTimers();
    usePlayerStore.setState({
      port: fakePort(),
      queue: Array.from({ length: 10 }, (_, i) => track(`t${i}`)),
      index: 0,
      sessionId: null,
      waveMeta: meta,
      lastPlayedAt: {},
      positionSeconds: 0,
      durationSeconds: null,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("a dislike advances and rebuilds the unplayed tail once", async () => {
    await usePlayerStore.getState().playIndex(0);
    await usePlayerStore.getState().dislikeCurrent();

    expect(usePlayerStore.getState().index).toBe(1);
    expect(patched.calls).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(ADAPT_DEBOUNCE_MS + 100);

    expect(patched.calls).toHaveLength(1);
    expect(patched.calls[0]?.path).toBe("/api/v1/waves/q1");
    const queue = usePlayerStore.getState().queue.map((item) => item.videoId);
    // Head (t0 started, t1 playing) survives; the tail is the adapted one.
    expect(queue.slice(0, 2)).toEqual(["t0", "t1"]);
    expect(queue.slice(2)).toEqual(["adapted-1", "adapted-2"]);
    expect(usePlayerStore.getState().waveMeta?.queueId).toBe("q-adapted");
  });

  it("an early explicit next adapts the tail", async () => {
    await usePlayerStore.getState().playIndex(0);
    usePlayerStore.setState({ positionSeconds: 20, durationSeconds: 200 });

    await usePlayerStore.getState().next();
    await vi.advanceTimersByTimeAsync(ADAPT_DEBOUNCE_MS + 100);

    expect(patched.calls).toHaveLength(1);
  });

  it("a late next is not a negative signal", async () => {
    await usePlayerStore.getState().playIndex(0);
    usePlayerStore.setState({ positionSeconds: 180, durationSeconds: 200 });

    await usePlayerStore.getState().next();
    await vi.advanceTimersByTimeAsync(ADAPT_DEBOUNCE_MS + 100);

    expect(patched.calls).toHaveLength(0);
  });

  it("a run of quick skips costs one rebuild", async () => {
    await usePlayerStore.getState().playIndex(0);

    for (let i = 0; i < 3; i += 1) {
      usePlayerStore.setState({ positionSeconds: 5, durationSeconds: 200 });
      await usePlayerStore.getState().next();
      await vi.advanceTimersByTimeAsync(300);
    }
    await vi.advanceTimersByTimeAsync(ADAPT_DEBOUNCE_MS + 100);

    expect(patched.calls).toHaveLength(1);
  });
});
