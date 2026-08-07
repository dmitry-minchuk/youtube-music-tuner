/**
 * Queue bookkeeping (docs/04 section 8): starting a track marks it played on
 * its own wave, and the queue asks for a local extension three tracks before
 * the end.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlayerPort } from "@/player/types";

const posted = vi.hoisted(() => ({ calls: [] as { path: string; body: unknown }[] }));

vi.mock("@/api/client", () => ({
  api: {
    post: (path: string, body?: unknown) => {
      posted.calls.push({ path, body });
      if (path.endsWith("/extend")) {
        return Promise.resolve({
          queueId: "q-ext",
          generationId: "g-ext",
          mix: { targetFamiliarPercent: 15, actualFamiliarPercent: 15, actualDiscoveryPercent: 85 },
          relaxations: [],
          items: [
            {
              position: 1,
              track: { videoId: "fresh-1", title: "Fresh 1", artists: [] },
              reasonCodes: [],
              familiarity: "DISCOVERY",
            },
            {
              position: 2,
              track: { videoId: "b", title: "Already queued", artists: [] },
              reasonCodes: [],
              familiarity: "DISCOVERY",
            },
          ],
        });
      }
      return Promise.resolve({});
    },
    get: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
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

import { usePlayerStore, type QueueTrack } from "@/player/playerStore";

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
