/** Player contract used everywhere except the iframe adapter (docs/04 s.2). */

export type PlayerState =
  | "UNSTARTED"
  | "ENDED"
  | "PLAYING"
  | "PAUSED"
  | "BUFFERING"
  | "CUED"
  | "ERROR";

export type PlayerEventType =
  | "ready"
  | "stateChange"
  | "error";

export interface PlayerEvent {
  type: PlayerEventType;
  state: PlayerState;
  currentTime: number;
  duration: number | null;
  errorCode?: number;
}

export interface PlayerPort {
  cue(videoId: string): Promise<void>;
  play(): void;
  pause(): void;
  seekTo(seconds: number): void;
  setVolume(percent: number): void;
  currentTime(): number;
  duration(): number;
  state(): PlayerState;
  subscribe(listener: (event: PlayerEvent) => void): () => void;
  destroy(): void;
}

/** Telemetry event names exchanged with the backend (docs/04 section 3). */
export type TelemetryEventType =
  | "track_cued"
  | "play_started"
  | "play_resumed"
  | "paused"
  | "buffering_started"
  | "progress_tick"
  | "seek_forward"
  | "seek_backward"
  | "next_clicked"
  | "previous_clicked"
  | "ended"
  | "replay_started"
  | "like_set"
  | "dislike_set"
  | "player_error"
  | "visibility_changed"
  | "page_closing";

export interface TelemetryEvent {
  clientEventId: string;
  sessionId: string;
  sequenceNo: number;
  videoId: string;
  type: TelemetryEventType;
  occurredAt: string;
  monotonicMs: number;
  payload: Record<string, unknown>;
}
