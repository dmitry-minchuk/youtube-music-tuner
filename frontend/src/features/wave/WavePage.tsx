import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { useLearningStatus } from "@/api/insights";
import { useSettings } from "@/api/settings";
import {
  MOODS,
  describeMix,
  expectedFamiliarPercent,
  useCreateWave,
  usePatchWave,
  type Mood,
  type WaveResponse,
} from "@/api/wave";
import { humanizeReason } from "@/player/PlayerPanel";
import { usePlayerStore } from "@/player/playerStore";
import { Button } from "@/ui/Button";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/wave/WavePage.module.css";

/** Retuning hits the ranker, so wait for the slider to settle. */
const RETUNE_DEBOUNCE_MS = 500;

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return "Late night";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function waveErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.body.reasonCode === "EMPTY_POOL") {
      return "No candidates yet. Sync your library from Settings, then let the first candidate refresh run.";
    }
    if (error.code === "YTM_AUTH_REQUIRED") return "YouTube Music sync paused — reconnect required.";
    return error.message;
  }
  return "Could not reach the local API.";
}

/** One message per wave, most consequential code first (docs/05 s.9-10). */
export function relaxationMessage(codes: string[]): string | null {
  if (codes.includes("DISCOVERY_POOL_WIDENED")) {
    return "Not enough fresh discovery candidates — topped up with familiar picks.";
  }
  if (codes.includes("FAMILIAR_POOL_WIDENED")) {
    return "Not enough familiar tracks yet — filled the gap with discovery picks.";
  }
  if (codes.includes("CONTEXT_WIDENED")) {
    return "Context widened — this context cannot narrow the selection yet.";
  }
  if (codes.length > 0) return "Diversity widened to fill the queue.";
  return null;
}

export function WavePage(): React.JSX.Element {
  const settings = useSettings();
  const [temperature, setTemperature] = useState(50);
  const [mood, setMood] = useState<Mood>("ANY");
  const retuneTimer = useRef<number | null>(null);

  const learning = useLearningStatus();
  const createWave = useCreateWave();
  const patchWave = usePatchWave();
  const setQueue = usePlayerStore((store) => store.setQueue);
  const playIndex = usePlayerStore((store) => store.playIndex);
  const currentIndex = usePlayerStore((store) => store.index);
  const playerState = usePlayerStore((store) => store.state);
  const currentVideoId = usePlayerStore((store) => store.queue[store.index]?.videoId);
  const unplayableSkipped = usePlayerStore((store) => store.unplayableSkipped);
  // The queue lives in the store: switching sections must not lose it.
  const queue = usePlayerStore((store) => store.queue);
  const waveMeta = usePlayerStore((store) => store.waveMeta);

  // Adopt saved defaults once, unless a wave is already running — then its
  // own settings win, so returning to the page shows what is actually playing.
  const defaultsApplied = useRef(false);
  useEffect(() => {
    if (defaultsApplied.current) return;
    if (waveMeta) {
      defaultsApplied.current = true;
      setTemperature(waveMeta.temperature);
      setMood(waveMeta.mood as Mood);
      return;
    }
    if (!settings.data) return;
    defaultsApplied.current = true;
    setTemperature(settings.data.defaultTemperature);
    setMood(settings.data.defaultMood);
  }, [settings.data, waveMeta]);

  useEffect(() => () => {
    if (retuneTimer.current !== null) window.clearTimeout(retuneTimer.current);
  }, []);

  const applyWave = (response: WaveResponse, usedTemperature: number, usedMood: Mood) => {
    setQueue(
      response.items.map((item) => ({
        videoId: item.track.videoId,
        title: item.track.title,
        artists: item.track.artists,
        durationSeconds: null,
        reasonCodes: item.reasonCodes,
        familiarity: item.familiarity,
      })),
      {
        queueId: response.queueId,
        generationId: response.generationId,
        targetFamiliarPercent: response.mix.targetFamiliarPercent,
        actualFamiliarPercent: response.mix.actualFamiliarPercent,
        relaxations: response.relaxations,
        temperature: usedTemperature,
        mood: usedMood,
      },
    );
  };

  const startWave = () => {
    // A retune scheduled for the previous queue must not fire after the new
    // wave lands and overwrite it (the PATCH would race the POST).
    if (retuneTimer.current !== null) {
      window.clearTimeout(retuneTimer.current);
      retuneTimer.current = null;
    }
    createWave.mutate(
      { temperature, mood },
      {
        onSuccess: (response) => {
          applyWave(response, temperature, mood);
          void playIndex(0);
        },
      },
    );
  };

  /** Rebuild only the unplayed tail; the slider stays instant either way. */
  const scheduleRetune = (nextTemperature: number, nextMood: Mood) => {
    const queueId = waveMeta?.queueId;
    if (!queueId) return;
    if (retuneTimer.current !== null) window.clearTimeout(retuneTimer.current);
    retuneTimer.current = window.setTimeout(() => {
      patchWave.mutate(
        { queueId, temperature: nextTemperature, mood: nextMood },
        {
          onSuccess: (response, variables) => {
            // The response carries a fresh queueId, so compare what the PATCH
            // targeted with what is on stage now: a retune of a replaced queue
            // must be dropped, not applied over the wave the user just started.
            if (variables.queueId !== usePlayerStore.getState().waveMeta?.queueId) return;
            applyWave(response, nextTemperature, nextMood);
          },
        },
      );
    }, RETUNE_DEBOUNCE_MS);
  };

  const onTemperatureChange = (value: number) => {
    setTemperature(value);
    scheduleRetune(value, mood);
  };

  const onMoodChange = (value: Mood) => {
    setMood(value);
    scheduleRetune(temperature, value);
  };

  // The server owns this label (docs/06 s.7): it knows the actual phase,
  // including "Model active" — a hardcoded string here kept saying "shadow"
  // long after the model had taken over.
  const learningLabel = learning.data?.label ?? "Learning status unavailable";

  // While retuning, show what the slider promises rather than a stale mix.
  const shownMix = patchWave.isPending
    ? expectedFamiliarPercent(temperature)
    : (waveMeta?.actualFamiliarPercent ?? expectedFamiliarPercent(temperature));

  return (
    <>
      <PageHeading title={greeting()} subtitle="Your Wave" />

      <Panel>
        <div className={styles.controls}>
          <label className={styles.mood}>
            <span className={styles.label}>Context</span>
            <select
              value={mood}
              onChange={(event) => onMoodChange(event.target.value as Mood)}
              disabled={createWave.isPending}
            >
              {MOODS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <div className={styles.temperature}>
            <div className={styles.temperatureHeader}>
              <span className={styles.label}>Discovery balance</span>
              <span className={styles.mixValue}>
                {describeMix(shownMix)}
                {patchWave.isPending && <span className={styles.retuning}> · retuning…</span>}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              value={temperature}
              onChange={(event) => onTemperatureChange(Number(event.target.value))}
              aria-label="Temperature"
              aria-valuetext={describeMix(expectedFamiliarPercent(temperature))}
            />
            <div className={styles.temperatureEnds} aria-hidden="true">
              <span>Familiar</span>
              <span>Discovery</span>
            </div>
          </div>

          <Button variant="primary" onClick={startWave} disabled={createWave.isPending}>
            {createWave.isPending
              ? "Preparing…"
              : queue.length > 0 && playerState === "PLAYING"
                ? "Restart Wave"
                : "Start Wave"}
          </Button>
        </div>

        <p className={styles.learning}>{learningLabel}</p>

        {createWave.isError && (
          <p className={styles.error} role="status">
            {waveErrorMessage(createWave.error)}
          </p>
        )}
        {patchWave.isError && (
          <p className={styles.error} role="status">
            Could not retune the queue — the current one is unchanged.
          </p>
        )}

        {unplayableSkipped > 0 && (
          <p className={styles.relaxation} role="status">
            Skipped {unplayableSkipped} track{unplayableSkipped === 1 ? "" : "s"} that YouTube does
            not allow to play in an embedded player. They will not be queued again.
          </p>
        )}

        {waveMeta && relaxationMessage(waveMeta.relaxations) && (
          <p className={styles.relaxation} role="status">
            {relaxationMessage(waveMeta.relaxations)}
          </p>
        )}
      </Panel>

      <Panel title="Up next">
        {queue.length === 0 && (
          <EmptyState message="Start the Wave to build a queue from your local pool." />
        )}
        {queue.length > 0 && (
          <ol className={styles.queue} aria-busy={patchWave.isPending}>
            {queue.map((item, index) => {
              const isCurrent = item.videoId === currentVideoId || index === currentIndex;
              return (
                <li key={item.videoId} className={isCurrent ? styles.currentItem : undefined}>
                  <button
                    type="button"
                    className={styles.queueRow}
                    onClick={() => void playIndex(index)}
                    aria-current={isCurrent ? "true" : undefined}
                  >
                    <span className={styles.position}>
                      {isCurrent && playerState === "PLAYING"
                        ? "▶"
                        : String(index + 1).padStart(2, "0")}
                    </span>
                    <span className={styles.queueTitle}>{item.title}</span>
                    <span className={styles.queueArtist}>{item.artists.join(", ")}</span>
                    <span
                      className={
                        item.familiarity === "FAMILIAR" ? styles.familiar : styles.discovery
                      }
                    >
                      {item.familiarity === "FAMILIAR" ? "familiar" : "discovery"}
                    </span>
                    <span className={styles.reason}>
                      {item.reasonCodes?.[0] ? humanizeReason(item.reasonCodes[0]) : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </>
  );
}
