/**
 * The only module that knows about the global `YT.Player` object.
 *
 * The player stays visible and at least 200x200 px; Tuner passes a videoId
 * and never touches a stream URL (docs/04 section 1).
 */

import type { PlayerEvent, PlayerPort, PlayerState } from "@/player/types";

const IFRAME_API_SRC = "https://www.youtube.com/iframe_api";

const STATE_BY_CODE: Record<number, PlayerState> = {
  [-1]: "UNSTARTED",
  0: "ENDED",
  1: "PLAYING",
  2: "PAUSED",
  3: "BUFFERING",
  5: "CUED",
};

interface YtPlayer {
  cueVideoById(videoId: string): void;
  loadVideoById(videoId: string): void;
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  setVolume(percent: number): void;
  getCurrentTime(): number;
  getDuration(): number;
  getPlayerState(): number;
  destroy(): void;
}

declare global {
  interface Window {
    YT?: {
      Player: new (element: HTMLElement | string, options: Record<string, unknown>) => YtPlayer;
      PlayerState: Record<string, number>;
    };
    onYouTubeIframeAPIReady?: () => void;
  }
}

let apiPromise: Promise<void> | null = null;

/** Load the official IFrame API exactly once. */
export function loadIframeApi(): Promise<void> {
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;

  apiPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector(`script[src="${IFRAME_API_SRC}"]`);
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      resolve();
    };
    if (existing) return;

    const script = document.createElement("script");
    script.src = IFRAME_API_SRC;
    script.async = true;
    script.onerror = () => reject(new Error("Could not load the YouTube IFrame API"));
    document.head.appendChild(script);
  });
  return apiPromise;
}

export interface IframeAdapterOptions {
  container: HTMLElement;
  /** Exact origin, taking the configurable port into account. */
  origin?: string;
}

export async function createIframePlayer(options: IframeAdapterOptions): Promise<PlayerPort> {
  await loadIframeApi();
  const YT = window.YT;
  if (!YT) throw new Error("YouTube IFrame API is unavailable");

  const listeners = new Set<(event: PlayerEvent) => void>();
  let currentState: PlayerState = "UNSTARTED";

  const emit = (type: PlayerEvent["type"], errorCode?: number) => {
    const duration = player?.getDuration() ?? 0;
    const event: PlayerEvent = {
      type,
      state: currentState,
      currentTime: player?.getCurrentTime() ?? 0,
      duration: duration > 0 ? duration : null,
      ...(errorCode === undefined ? {} : { errorCode }),
    };
    listeners.forEach((listener) => listener(event));
  };

  let player: YtPlayer | undefined;

  await new Promise<void>((resolve) => {
    player = new YT.Player(options.container, {
      width: "100%",
      height: "100%",
      playerVars: {
        // Keep the required controls and branding visible.
        controls: 1,
        disablekb: 0,
        modestbranding: 1,
        rel: 0,
        playsinline: 1,
        origin: options.origin ?? window.location.origin,
      },
      events: {
        onReady: () => {
          currentState = "CUED";
          emit("ready");
          resolve();
        },
        onStateChange: (event: { data: number }) => {
          currentState = STATE_BY_CODE[event.data] ?? "UNSTARTED";
          emit("stateChange");
        },
        onError: (event: { data: number }) => {
          currentState = "ERROR";
          emit("error", event.data);
        },
      },
    });
  });

  const active = player!;

  return {
    async cue(videoId: string) {
      active.loadVideoById(videoId);
    },
    play: () => active.playVideo(),
    pause: () => active.pauseVideo(),
    seekTo: (seconds: number) => active.seekTo(seconds, true),
    setVolume: (percent: number) => active.setVolume(Math.max(0, Math.min(100, percent))),
    currentTime: () => active.getCurrentTime(),
    duration: () => active.getDuration(),
    state: () => currentState,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy() {
      listeners.clear();
      active.destroy();
    },
  };
}
