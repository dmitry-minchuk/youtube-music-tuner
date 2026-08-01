/**
 * Measures genuinely played seconds (docs/04 section 4).
 *
 * A one second sampler adds an interval only when the player was PLAYING
 * across it, the monotonic delta is plausible, the position advanced in step
 * with the clock (so a seek cannot fake listening), and nothing was
 * buffering. Counters are cumulative, so a resent tick never double counts.
 */

import type { PlayerState } from "@/player/types";

export const SAMPLE_INTERVAL_MS = 1000;
export const MAX_PLAUSIBLE_DELTA_SECONDS = 2.5;
/** Position drift beyond this within one sample means a seek, not playback. */
export const SEEK_TOLERANCE_SECONDS = 1.5;
export const PROGRESS_TICK_SECONDS = 15;

export type DurationSource = "PLAYER" | "METADATA" | "UNKNOWN";

export interface Sample {
  monotonicMs: number;
  state: PlayerState;
  position: number;
  duration: number | null;
}

export interface TrackerCounters {
  playedSeconds: number;
  positionSeconds: number;
  maxPositionSeconds: number;
  seekForwardSeconds: number;
  seekBackwardSeconds: number;
  seekForwardCount: number;
  seekBackwardCount: number;
  bufferedSeconds: number;
  wallClockSeconds: number;
  effectiveDurationSeconds: number | null;
  durationSource: DurationSource;
}

export interface SeekObservation {
  direction: "forward" | "backward";
  seconds: number;
}

export class PlaybackTracker {
  private previous: Sample | null = null;
  private startMonotonicMs: number | null = null;
  private lastTickAtPlayedSeconds = 0;

  private counters: TrackerCounters = {
    playedSeconds: 0,
    positionSeconds: 0,
    maxPositionSeconds: 0,
    seekForwardSeconds: 0,
    seekBackwardSeconds: 0,
    seekForwardCount: 0,
    seekBackwardCount: 0,
    bufferedSeconds: 0,
    wallClockSeconds: 0,
    effectiveDurationSeconds: null,
    durationSource: "UNKNOWN",
  };

  constructor(metadataDuration: number | null = null) {
    if (metadataDuration && metadataDuration > 0) {
      this.counters.effectiveDurationSeconds = metadataDuration;
      this.counters.durationSource = "METADATA";
    }
  }

  get snapshot(): TrackerCounters {
    return { ...this.counters };
  }

  /** Feed one sample; returns the seek it implies, if any. */
  observe(sample: Sample): SeekObservation | null {
    if (this.startMonotonicMs === null) this.startMonotonicMs = sample.monotonicMs;
    this.counters.wallClockSeconds = (sample.monotonicMs - this.startMonotonicMs) / 1000;

    // The player is the authoritative duration source once it reports one.
    if (sample.duration && sample.duration > 0) {
      this.counters.effectiveDurationSeconds = sample.duration;
      this.counters.durationSource = "PLAYER";
    }

    this.counters.positionSeconds = sample.position;
    this.counters.maxPositionSeconds = Math.max(
      this.counters.maxPositionSeconds,
      sample.position,
    );

    const previous = this.previous;
    this.previous = sample;
    if (!previous) return null;

    const deltaSeconds = (sample.monotonicMs - previous.monotonicMs) / 1000;
    const positionDelta = sample.position - previous.position;

    if (sample.state === "BUFFERING" || previous.state === "BUFFERING") {
      if (deltaSeconds > 0 && deltaSeconds <= MAX_PLAUSIBLE_DELTA_SECONDS) {
        this.counters.bufferedSeconds += deltaSeconds;
      }
      return null;
    }

    const seek = this.detectSeek(deltaSeconds, positionDelta);
    if (seek) return seek;

    const bothPlaying = previous.state === "PLAYING" && sample.state === "PLAYING";
    const plausibleClock = deltaSeconds > 0 && deltaSeconds <= MAX_PLAUSIBLE_DELTA_SECONDS;
    const positionAgrees = Math.abs(positionDelta - deltaSeconds) <= SEEK_TOLERANCE_SECONDS;

    if (bothPlaying && plausibleClock && positionAgrees) {
      this.counters.playedSeconds += deltaSeconds;
    }
    return null;
  }

  private detectSeek(deltaSeconds: number, positionDelta: number): SeekObservation | null {
    const drift = positionDelta - deltaSeconds;
    if (drift > SEEK_TOLERANCE_SECONDS) {
      this.counters.seekForwardSeconds += drift;
      this.counters.seekForwardCount += 1;
      return { direction: "forward", seconds: drift };
    }
    if (-drift > SEEK_TOLERANCE_SECONDS && positionDelta < 0) {
      const amount = Math.abs(positionDelta);
      this.counters.seekBackwardSeconds += amount;
      this.counters.seekBackwardCount += 1;
      return { direction: "backward", seconds: amount };
    }
    return null;
  }

  /** True once another 15 seconds of real listening have accumulated. */
  shouldEmitTick(): boolean {
    return this.counters.playedSeconds - this.lastTickAtPlayedSeconds >= PROGRESS_TICK_SECONDS;
  }

  markTickEmitted(): void {
    this.lastTickAtPlayedSeconds = this.counters.playedSeconds;
  }

  /** Payload shared by progress ticks and terminal events. */
  payload(): Record<string, unknown> {
    const counters = this.counters;
    return {
      playedSeconds: Number(counters.playedSeconds.toFixed(3)),
      positionSeconds: Number(counters.positionSeconds.toFixed(3)),
      maxPositionSeconds: Number(counters.maxPositionSeconds.toFixed(3)),
      seekForwardSeconds: Number(counters.seekForwardSeconds.toFixed(3)),
      seekBackwardSeconds: Number(counters.seekBackwardSeconds.toFixed(3)),
      seekForwardCount: counters.seekForwardCount,
      seekBackwardCount: counters.seekBackwardCount,
      bufferedSeconds: Number(counters.bufferedSeconds.toFixed(3)),
      wallClockSeconds: Number(counters.wallClockSeconds.toFixed(3)),
      effectiveDurationSeconds: counters.effectiveDurationSeconds,
      durationSource: counters.durationSource,
    };
  }
}
