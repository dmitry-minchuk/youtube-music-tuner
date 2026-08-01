/**
 * Durable event outbox (docs/04 section 6).
 *
 * Events live in IndexedDB until the backend confirms them, so a reload or a
 * crash loses at most the interval since the last created tick. Falls back to
 * memory when IndexedDB is unavailable.
 */

import type { TelemetryEvent } from "@/player/types";

const DB_NAME = "tuner-telemetry";
const DB_VERSION = 1;
const STORE = "outbox";

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "clientEventId" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
  });
}

export class TelemetryOutbox {
  private dbPromise: Promise<IDBDatabase | null> | null = null;
  private memory = new Map<string, TelemetryEvent>();

  private db(): Promise<IDBDatabase | null> {
    this.dbPromise ??= openDatabase();
    return this.dbPromise;
  }

  async add(event: TelemetryEvent): Promise<void> {
    this.memory.set(event.clientEventId, event);
    const db = await this.db();
    if (!db) return;
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(event);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  }

  async pending(limit = 100): Promise<TelemetryEvent[]> {
    const db = await this.db();
    if (!db) return [...this.memory.values()].slice(0, limit);

    const stored = await new Promise<TelemetryEvent[]>((resolve) => {
      const tx = db.transaction(STORE, "readonly");
      const request = tx.objectStore(STORE).getAll();
      request.onsuccess = () => resolve(request.result as TelemetryEvent[]);
      request.onerror = () => resolve([]);
    });

    const merged = new Map(this.memory);
    stored.forEach((event) => merged.set(event.clientEventId, event));
    return [...merged.values()]
      .sort((a, b) => a.sequenceNo - b.sequenceNo)
      .slice(0, limit);
  }

  /** Drop only what the backend acknowledged. */
  async confirm(eventIds: string[]): Promise<void> {
    eventIds.forEach((id) => this.memory.delete(id));
    const db = await this.db();
    if (!db) return;
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      eventIds.forEach((id) => store.delete(id));
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  }

  async size(): Promise<number> {
    return (await this.pending(1000)).length;
  }
}
