import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const playerBarFixture = vi.hoisted(() => ({
  state: "PAUSED" as "PAUSED" | "BUFFERING",
  rating: "LIKE" as "LIKE" | "DISLIKE",
  setRating: vi.fn(),
  rate: vi.fn(),
  dislikeCurrent: vi.fn(),
}));

vi.mock("@/api/library", () => ({
  formatDuration: (seconds: number | null) => (seconds === null ? "--:--" : `0:${seconds}`),
  useSetRating: () => ({ mutate: playerBarFixture.setRating }),
  useTrackRating: () => ({
    data: { desiredState: playerBarFixture.rating, syncStatus: "SYNCED", vetoed: false },
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
    rate: playerBarFixture.rate,
    dislikeCurrent: playerBarFixture.dislikeCurrent,
  };
  const usePlayerStore = Object.assign(
    (selector: (value: typeof store) => unknown) => selector(store),
    { getState: () => store },
  );

  return { currentTrack: () => track, usePlayerStore };
});

import { PlayerBar, nextRating } from "@/player/PlayerBar";

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

describe("rating toggle", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    delete (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
  });

  it("clears an active rating instead of setting it again", () => {
    expect(nextRating("DISLIKE", true)).toBe("INDIFFERENT");
    expect(nextRating("LIKE", true)).toBe("INDIFFERENT");
    expect(nextRating("DISLIKE", false)).toBe("DISLIKE");
    expect(nextRating("LIKE", false)).toBe("LIKE");
  });

  it.each([
    ["LIKE", "Remove like"],
    ["DISLIKE", "Remove dislike"],
  ] as const)("removing a %s sends no like/dislike signal", async (rating, label) => {
    playerBarFixture.rating = rating;
    playerBarFixture.rate.mockClear();
    playerBarFixture.dislikeCurrent.mockClear();
    playerBarFixture.setRating.mockClear();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(<PlayerBar />));

    await act(async () => {
      container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)?.click();
    });

    expect(playerBarFixture.setRating).toHaveBeenCalledWith({
      videoId: "track-1",
      desiredState: "INDIFFERENT",
    });
    expect(playerBarFixture.rate).not.toHaveBeenCalled();
    expect(playerBarFixture.dislikeCurrent).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    container.remove();
    playerBarFixture.rating = "LIKE";
  });
});
