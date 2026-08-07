import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const playerBarFixture = vi.hoisted(() => ({ state: "PAUSED" as "PAUSED" | "BUFFERING" }));

vi.mock("@/api/library", () => ({
  formatDuration: (seconds: number | null) => (seconds === null ? "--:--" : `0:${seconds}`),
  useSetRating: () => ({ mutate: vi.fn() }),
  useTrackRating: () => ({
    data: { desiredState: "LIKE", syncStatus: "SYNCED", vetoed: false },
  }),
  useVeto: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/player/playerStore", () => {
  const track = { videoId: "track-1", title: "Track", artists: ["Artist"] };
  const store = {
    get state() {
      return playerBarFixture.state;
    },
    positionSeconds: 7,
    durationSeconds: 226,
    volume: 80,
    pendingEvents: 3,
    port: {},
    previous: vi.fn(),
    next: vi.fn(),
    togglePlay: vi.fn(),
    seekTo: vi.fn(),
    setVolume: vi.fn(),
    rate: vi.fn(),
  };
  const usePlayerStore = Object.assign(
    (selector: (value: typeof store) => unknown) => selector(store),
    { getState: () => store },
  );

  return { currentTrack: () => track, usePlayerStore };
});

import { PlayerBar } from "@/player/PlayerBar";

describe("PlayerBar layout", () => {
  beforeEach(() => {
    playerBarFixture.state = "PAUSED";
  });

  it("places every control row directly on one shared grid", () => {
    document.body.innerHTML = renderToStaticMarkup(<PlayerBar />);

    const bar = document.body.firstElementChild;
    const play = document.querySelector<HTMLButtonElement>('button[aria-label="Play"]');
    const like = document.querySelector<HTMLButtonElement>('button[aria-label="Remove like"]');
    const seek = document.querySelector<HTMLInputElement>('input[aria-label="Seek"]');
    const volume = document.querySelector<HTMLInputElement>('input[aria-valuetext^="Volume"]');

    expect(bar).not.toBeNull();
    expect(play?.parentElement?.parentElement).toBe(bar);
    expect(like?.parentElement?.parentElement).toBe(bar);
    expect(seek?.parentElement?.parentElement).toBe(bar);
    expect(volume?.closest("label")?.parentElement?.parentElement).toBe(bar);

    expect(play?.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("keeps buffering feedback inside the centred play control", () => {
    playerBarFixture.state = "BUFFERING";
    document.body.innerHTML = renderToStaticMarkup(<PlayerBar />);

    const play = document.querySelector<HTMLButtonElement>('button[aria-label="Play"]');
    expect(play?.textContent).toBe("…");
    expect(play?.querySelector("span")?.textContent).toBe("…");
    expect(play?.querySelector("svg")).toBeNull();
  });
});
