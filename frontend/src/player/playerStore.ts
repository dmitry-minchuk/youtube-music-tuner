/**
 * Player and queue state (docs/04 sections 1, 5 and 8).
 *
 * One playback session per videoId: it starts on the first PLAYING and is
 * closed atomically before the next track starts. Leaving the tab pauses
 * playback — a policy requirement, not an optimisation.
 */

import { create } from "zustand";
import { PlaybackTracker, SAMPLE_INTERVAL_MS } from "@/player/playbackTracker";
import { TelemetryClient } from "@/player/telemetryClient";
import type { PlayerPort, PlayerState, TelemetryEventType } from "@/player/types";

export interface WaveMeta {
  queueId: string;
  generationId: string;
  targetFamiliarPercent: number;
  actualFamiliarPercent: number;
  relaxations: string[];
  temperature: number;
  mood: string;
}

export interface QueueTrack {
  videoId: string;
  title: string;
  artists: string[];
  durationSeconds: number | null;
  thumbnailUrl?: string | null;
  reasonCodes?: string[];
  familiarity?: "FAMILIAR" | "DISCOVERY";
}

interface PlayerStoreState {
  port: PlayerPort | null;
  state: PlayerState;
  queue: QueueTrack[];
  queueId: string | null;
  generationId: string | null;
  waveMeta: WaveMeta | null;
  index: number;
  sessionId: string | null;
  positionSeconds: number;
  durationSeconds: number | null;
  volume: number;
  pauseOnHidden: boolean;
  pausedByPolicy: boolean;
  unplayableSkipped: number;
  pendingEvents: number;
  lastPlayedAt: Record<string, number>;

  attachPort: (port: PlayerPort) => void;
  detachPort: () => void;
  setQueue: (tracks: QueueTrack[], meta?: WaveMeta) => void;
  playIndex: (index: number) => Promise<void>;
  togglePlay: () => void;
  next: () => Promise<void>;
  previous: () => Promise<void>;
  seekTo: (seconds: number) => void;
  setVolume: (percent: number) => void;
  setPauseOnHidden: (value: boolean) => void;
  rate: (rating: "LIKE" | "DISLIKE") => Promise<void>;
  handleVisibilityChange: (hidden: boolean) => void;
  handlePageHide: () => void;
}

const telemetry = new TelemetryClient();
let tracker: PlaybackTracker | null = null;
let sampleTimer: number | null = null;
let unsubscribe: (() => void) | null = null;

export const currentTrack = (state: PlayerStoreState): QueueTrack | null =>
  state.queue[state.index] ?? null;

export const usePlayerStore = create<PlayerStoreState>((set, get) => {
  async function emit(type: TelemetryEventType, extra: Record<string, unknown> = {}) {
    const { sessionId, queueId, generationId } = get();
    const track = currentTrack(get());
    if (!sessionId || !track) return;
    await telemetry.record(sessionId, track.videoId, type, {
      ...(tracker?.payload() ?? {}),
      ...(queueId ? { queueId } : {}),
      ...(generationId ? { generationId } : {}),
      ...extra,
    });
    set({ pendingEvents: await telemetry.pendingCount() });
  }

  async function emitAndFlush(type: TelemetryEventType, extra: Record<string, unknown> = {}) {
    await emit(type, extra);
    await telemetry.flush();
    set({ pendingEvents: await telemetry.pendingCount() });
  }

  function stopSampling() {
    if (sampleTimer !== null) {
      window.clearInterval(sampleTimer);
      sampleTimer = null;
    }
  }

  function startSampling() {
    stopSampling();
    sampleTimer = window.setInterval(() => {
      const { port } = get();
      if (!port || !tracker) return;

      const duration = port.duration();
      const seek = tracker.observe({
        monotonicMs: performance.now(),
        state: port.state(),
        position: port.currentTime(),
        duration: duration > 0 ? duration : null,
      });

      set({
        positionSeconds: port.currentTime(),
        durationSeconds: duration > 0 ? duration : null,
      });

      if (seek) {
        void emit(seek.direction === "forward" ? "seek_forward" : "seek_backward", {
          seekSeconds: Number(seek.seconds.toFixed(3)),
        });
      }
      if (tracker.shouldEmitTick()) {
        tracker.markTickEmitted();
        void emitAndFlush("progress_tick", { playerState: port.state() });
      }
    }, SAMPLE_INTERVAL_MS);
  }

  /** Close the current session so the next track starts cleanly. */
  async function closeSession(reason: TelemetryEventType | null) {
    if (!get().sessionId) return;
    if (reason) await emitAndFlush(reason);
    else await telemetry.flush();
    stopSampling();
    tracker = null;
    set({ sessionId: null });
  }

  /** Move on without claiming the user pressed next (docs/04 s.3). */
  async function advance() {
    await closeSession(null);
    const nextIndex = get().index + 1;
    if (nextIndex < get().queue.length) await startTrack(nextIndex);
  }

  async function startTrack(index: number) {
    const { queue, port } = get();
    const track = queue[index];
    if (!track || !port) return;

    const previouslyPlayedAt = get().lastPlayedAt[track.videoId];
    const isReplay =
      previouslyPlayedAt !== undefined && Date.now() - previouslyPlayedAt < 10 * 60 * 1000;

    tracker = new PlaybackTracker(track.durationSeconds);
    set({
      index,
      sessionId: crypto.randomUUID(),
      positionSeconds: 0,
      durationSeconds: track.durationSeconds,
      pausedByPolicy: false,
      lastPlayedAt: { ...get().lastPlayedAt, [track.videoId]: Date.now() },
    });

    await emit("track_cued");
    if (isReplay) await emit("replay_started");
    await port.cue(track.videoId);
    port.play();
    startSampling();
  }

  return {
    port: null,
    state: "UNSTARTED",
    queue: [],
    queueId: null,
    generationId: null,
    waveMeta: null,
    index: 0,
    sessionId: null,
    positionSeconds: 0,
    durationSeconds: null,
    volume: 80,
    pauseOnHidden: true,
    pausedByPolicy: false,
    unplayableSkipped: 0,
    pendingEvents: 0,
    lastPlayedAt: {},

    attachPort(port) {
      unsubscribe?.();
      set({ port });
      port.setVolume(get().volume);
      unsubscribe = port.subscribe((event) => {
        set({ state: event.state });
        if (event.state === "PLAYING") void emit("play_started");
        if (event.state === "PAUSED") void emitAndFlush("paused");
        if (event.state === "BUFFERING") void emit("buffering_started");
        if (event.state === "ERROR") {
          // Not playable here (embedding disabled, region block). Neutral for
          // taste: advance without recording a skip.
          void (async () => {
            await emitAndFlush("player_error", { errorCode: event.errorCode });
            // 101/150: the owner disallowed embedding. Surface it so a run of
            // skips does not look like the player is broken.
            if (event.errorCode === 101 || event.errorCode === 150) {
              set({ unplayableSkipped: get().unplayableSkipped + 1 });
            }
            await advance();
          })();
        }
        if (event.state === "ENDED") {
          void (async () => {
            await emitAndFlush("ended");
            await advance();
          })();
        }
      });
    },

    detachPort() {
      unsubscribe?.();
      unsubscribe = null;
      stopSampling();
      set({ port: null });
    },

    setQueue(tracks, meta) {
      set({
        unplayableSkipped: 0,
        queue: tracks,
        queueId: meta?.queueId ?? get().queueId,
        generationId: meta?.generationId ?? get().generationId,
        waveMeta: meta ?? get().waveMeta,
      });
    },

    async playIndex(index) {
      await closeSession(null);
      await startTrack(index);
    },

    togglePlay() {
      const { port, state } = get();
      if (!port) return;
      if (state === "PLAYING") port.pause();
      else port.play();
    },

    async next() {
      // The explicit next is what turns a short listen into a skip.
      await closeSession("next_clicked");
      const nextIndex = get().index + 1;
      if (nextIndex < get().queue.length) await startTrack(nextIndex);
    },

    async previous() {
      await closeSession("previous_clicked");
      const previousIndex = Math.max(0, get().index - 1);
      await startTrack(previousIndex);
    },

    seekTo(seconds) {
      get().port?.seekTo(seconds);
    },

    setVolume(percent) {
      set({ volume: percent });
      get().port?.setVolume(percent);
    },

    setPauseOnHidden(value) {
      set({ pauseOnHidden: value });
    },

    async rate(rating) {
      const track = currentTrack(get());
      if (!track) return;
      await emitAndFlush(rating === "LIKE" ? "like_set" : "dislike_set");
    },

    handleVisibilityChange(hidden) {
      const { port, state, pauseOnHidden } = get();
      if (!port) return;
      void emit("visibility_changed", { state: hidden ? "hidden" : "visible" });
      // Browsers also report "hidden" when the window is merely occluded by
      // another application, so this is a user preference rather than a hard
      // rule; the default keeps the policy-safe behaviour.
      if (hidden && pauseOnHidden && state === "PLAYING") {
        port.pause();
        set({ pausedByPolicy: true });
        void telemetry.flush();
      }
      if (!hidden) set({ pausedByPolicy: false });
    },

    handlePageHide() {
      void telemetry
        .record(get().sessionId ?? "unknown", currentTrack(get())?.videoId ?? "unknown", "page_closing", {
          ...(tracker?.payload() ?? {}),
        })
        .then(() => telemetry.flushWithBeacon());
    },
  };
});
