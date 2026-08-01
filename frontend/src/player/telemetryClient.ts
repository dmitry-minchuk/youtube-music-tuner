/**
 * Builds telemetry events and ships them in batches (docs/04 section 6).
 *
 * Flush happens after every created progress tick and on track change,
 * like/dislike, pause and visibility change. `pagehide` uses sendBeacon.
 */

import { api } from "@/api/client";
import { TelemetryOutbox } from "@/player/outbox";
import type { TelemetryEvent, TelemetryEventType } from "@/player/types";

const BATCH_ENDPOINT = "/api/v1/telemetry/events:batch";
/** sendBeacon cannot set the CSRF header; this path uses the session cookie. */
const BEACON_ENDPOINT = "/api/v1/telemetry/events:beacon";
const MAX_BATCH = 100;

interface BatchResponse {
  accepted: number;
  duplicates: number;
  rejected: { clientEventId: string; code: string }[];
  serverTime: string;
}

export class TelemetryClient {
  private readonly outbox = new TelemetryOutbox();
  private sequence = 0;
  private sending = false;

  constructor(private readonly now: () => number = () => performance.now()) {}

  async record(
    sessionId: string,
    videoId: string,
    type: TelemetryEventType,
    payload: Record<string, unknown> = {},
  ): Promise<TelemetryEvent> {
    const event: TelemetryEvent = {
      clientEventId: crypto.randomUUID(),
      sessionId,
      sequenceNo: ++this.sequence,
      videoId,
      type,
      occurredAt: new Date().toISOString(),
      monotonicMs: Math.round(this.now()),
      payload,
    };
    await this.outbox.add(event);
    return event;
  }

  async flush(): Promise<void> {
    if (this.sending) return;
    this.sending = true;
    try {
      const events = await this.outbox.pending(MAX_BATCH);
      if (events.length === 0) return;

      const response = await api.post<BatchResponse>(BATCH_ENDPOINT, {
        schemaVersion: 1,
        events,
      });

      // Accepted and duplicate events are both settled; rejected ones would
      // never be accepted, so they are dropped rather than retried forever.
      const rejected = new Set(response.rejected.map((item) => item.clientEventId));
      await this.outbox.confirm(events.map((event) => event.clientEventId));
      if (rejected.size > 0) {
        console.warn(`Telemetry rejected ${rejected.size} event(s)`);
      }
    } catch {
      // Keep everything in the outbox and retry on the next flush.
    } finally {
      this.sending = false;
    }
  }

  /** Last-chance delivery during pagehide. */
  flushWithBeacon(): void {
    void this.outbox.pending(MAX_BATCH).then((events) => {
      if (events.length === 0) return;
      const body = JSON.stringify({ schemaVersion: 1, events });
      if (navigator.sendBeacon) {
        const blob = new Blob([body], { type: "application/json" });
        navigator.sendBeacon(BEACON_ENDPOINT, blob);
      }
    });
  }

  pendingCount(): Promise<number> {
    return this.outbox.size();
  }
}
