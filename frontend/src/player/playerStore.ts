/**
 * Player and queue state (docs/04 sections 1, 5 and 8).
 *
 * One playback session per videoId: it starts on the first PLAYING and is
 * closed atomically before the next track starts. Playback continues when the
 * tab is hidden unless the owner opts into pausing (docs/04 section 1).
 */

import { create } from "zustand";
import { api } from "@/api/client";
import type { WaveResponse } from "@/api/wave";
import { PlaybackTracker, SAMPLE_INTERVAL_MS } from "@/player/playbackTracker";
import { TelemetryClient } from "@/player/telemetryClient";
import type { PlayerPort, PlayerState, TelemetryEventType } from "@/player/types";

/** How close to the end the queue may get before it asks for more
 * locally ranked material (docs/04 section 8: three tracks). */
export const EXTEND_REMAINING_THRESHOLD = 3;

/** Debounce for the adaptive tail retune: a run of quick skips should cost
 * one rebuild, and the pause also lets the skip's telemetry land in the
 * aggregates the retune will rank with (docs/04 section 8). */
export const ADAPT_DEBOUNCE_MS = 1500;

/** An explicit next this early counts as a negative signal — the same
 * boundaries the backend uses to classify a skip (aggregation.py). */
export const NEGATIVE_SKIP_RATIO = 0.6;
export const NEGATIVE_SKIP_ABSOLUTE_SECONDS = 120;

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
  /** The wave that delivered this item; extensions bring their own id. */
  queueId?: string;
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
  dislikeCurrent: () => Promise<void>;
  vetoCurrent: () => Promise<void>;
  handleVisibilityChange: (hidden: boolean) => void;
  handlePageHide: () => void;
}

const telemetry = new TelemetryClient();
let tracker: PlaybackTracker | null = null;
let sampleTimer: number | null = null;
let unsubscribe: (() => void) | null = null;

export const currentTrack = (state: PlayerStoreState): QueueTrack | null =>
  state.queue[state.index] ?? null;

/**
 * Keep everything that already started (the history must not be reshuffled,
 * docs/04 s.8) and replace the rest with the retuned tail, deduplicated so a
 * track cannot appear twice.
 */
export function mergeRetunedQueue(
  queue: QueueTrack[],
  index: number,
  lastPlayedAt: Record<string, number>,
  retuned: QueueTrack[],
): QueueTrack[] {
  const current = queue[index];
  const headEnd = current && lastPlayedAt[current.videoId] !== undefined ? index + 1 : index;
  const head = queue.slice(0, headEnd);
  const known = new Set(head.map((track) => track.videoId));
  return [...head, ...retuned.filter((track) => !known.has(track.videoId))];
}

function toQueueTracks(response: WaveResponse): QueueTrack[] {
  return response.items.map((item) => ({
    videoId: item.track.videoId,
    title: item.track.title,
    artists: item.track.artists,
    durationSeconds: null,
    reasonCodes: item.reasonCodes,
    familiarity: item.familiarity,
    queueId: response.queueId,
  }));
}

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

  let extending = false;

  /** Three tracks before the end, ask for more locally ranked material
   * (docs/04 s.8). Purely local on the backend side — never a YouTube call. */
  async function maybeExtend() {
    const { queue, index, waveMeta } = get();
    if (extending || !waveMeta || queue.length === 0) return;
    if (queue.length - 1 - index > EXTEND_REMAINING_THRESHOLD) return;
    extending = true;
    try {
      const response = await api.post<WaveResponse>(
        `/api/v1/waves/${encodeURIComponent(waveMeta.queueId)}/extend`,
      );
      const known = new Set(get().queue.map((item) => item.videoId));
      const appended = toQueueTracks(response).filter((item) => !known.has(item.videoId));
      if (appended.length > 0) set({ queue: [...get().queue, ...appended] });
    } catch {
      // The queue simply ends where it ends; the next wave is one click away.
    } finally {
      extending = false;
    }
  }

  let adaptTimer: number | null = null;
  let adapting = false;

  /** A negative signal reshapes the unplayed tail (docs/04 s.8): the wave
   * adapts on this very track, the way the reference product does. Debounced
   * so a run of skips costs one rebuild and the telemetry lands first. */
  function scheduleAdaptiveRetune() {
    if (!get().waveMeta) return;
    if (adaptTimer !== null) window.clearTimeout(adaptTimer);
    adaptTimer = window.setTimeout(() => {
      adaptTimer = null;
      void adaptiveRetune();
    }, ADAPT_DEBOUNCE_MS);
  }

  async function adaptiveRetune() {
    const meta = get().waveMeta;
    if (!meta || adapting) return;
    adapting = true;
    try {
      // An empty PATCH keeps the wave's own temperature and mood.
      const response = await api.patch<WaveResponse>(
        `/api/v1/waves/${encodeURIComponent(meta.queueId)}`,
        {},
      );
      const state = get();
      // A fresh wave or a manual retune superseded this one: drop it.
      if (state.waveMeta?.queueId !== meta.queueId) return;
      const merged = mergeRetunedQueue(
        state.queue,
        state.index,
        state.lastPlayedAt,
        toQueueTracks(response),
      );
      set({
        queue: merged,
        queueId: response.queueId,
        generationId: response.generationId,
        waveMeta: {
          ...meta,
          queueId: response.queueId,
          generationId: response.generationId,
          targetFamiliarPercent: response.mix.targetFamiliarPercent,
          actualFamiliarPercent: response.mix.actualFamiliarPercent,
          relaxations: response.relaxations,
        },
      });
    } catch {
      // The current tail keeps playing; the next signal will try again.
    } finally {
      adapting = false;
    }
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

    // Queue bookkeeping, not telemetry: a started track is marked played so
    // a retune never re-offers it and Insights can count served items. It
    // must never interrupt playback.
    if (track.queueId) {
      void api
        .post(`/api/v1/waves/${encodeURIComponent(track.queueId)}/played`, {
          videoId: track.videoId,
        })
        .catch(() => undefined);
    }
    void maybeExtend();

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
    pauseOnHidden: false,
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
      // The explicit next is what turns a short listen into a skip — and an
      // early one is a negative signal the tail should adapt to (docs/04 s.8).
      const { sessionId, positionSeconds, durationSeconds } = get();
      const negative =
        sessionId !== null &&
        (durationSeconds !== null && durationSeconds > 0
          ? positionSeconds / durationSeconds < NEGATIVE_SKIP_RATIO
          : positionSeconds < NEGATIVE_SKIP_ABSOLUTE_SECONDS);
      await closeSession("next_clicked");
      if (negative) scheduleAdaptiveRetune();
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

    /** A dislike means "move on" — staying on a track just rejected makes
     * no sense (docs/04 s.8). The rating PUT itself stays in the UI layer. */
    async dislikeCurrent() {
      const track = currentTrack(get());
      if (!track) return;
      await emitAndFlush("dislike_set");
      scheduleAdaptiveRetune();
      await get().next();
    },

    /** "Don't Like At All": a dislike-grade signal on the real session, then
     * move on — nobody presses this to keep listening (docs/05 s.11). */
    async vetoCurrent() {
      const track = currentTrack(get());
      if (!track) return;
      await emitAndFlush("veto_set");
      scheduleAdaptiveRetune();
      await get().next();
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
