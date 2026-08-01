import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { useSystemStatus } from "@/api/hooks";
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

export function WavePage(): React.JSX.Element {
  const settings = useSettings();
  const [temperature, setTemperature] = useState(50);
  const [mood, setMood] = useState<Mood>("ANY");
  const [wave, setWave] = useState<WaveResponse | null>(null);
  const retuneTimer = useRef<number | null>(null);

  const status = useSystemStatus();
  const createWave = useCreateWave();
  const patchWave = usePatchWave();
  const setQueue = usePlayerStore((store) => store.setQueue);
  const playIndex = usePlayerStore((store) => store.playIndex);
  const currentIndex = usePlayerStore((store) => store.index);
  const playerState = usePlayerStore((store) => store.state);
  const currentVideoId = usePlayerStore((store) => store.queue[store.index]?.videoId);
  const unplayableSkipped = usePlayerStore((store) => store.unplayableSkipped);

  // Adopt the saved defaults once, without fighting later edits.
  const defaultsApplied = useRef(false);
  useEffect(() => {
    if (defaultsApplied.current || !settings.data) return;
    defaultsApplied.current = true;
    setTemperature(settings.data.defaultTemperature);
    setMood(settings.data.defaultMood);
  }, [settings.data]);

  useEffect(() => () => {
    if (retuneTimer.current !== null) window.clearTimeout(retuneTimer.current);
  }, []);

  const applyWave = (response: WaveResponse) => {
    setWave(response);
    setQueue(
      response.items.map((item) => ({
        videoId: item.track.videoId,
        title: item.track.title,
        artists: item.track.artists,
        durationSeconds: null,
        reasonCodes: item.reasonCodes,
        familiarity: item.familiarity,
      })),
      { queueId: response.queueId, generationId: response.generationId },
    );
  };

  const startWave = () => {
    createWave.mutate(
      { temperature, mood },
      {
        onSuccess: (response) => {
          applyWave(response);
          void playIndex(0);
        },
      },
    );
  };

  /** Rebuild only the unplayed tail; the slider stays instant either way. */
  const scheduleRetune = (nextTemperature: number, nextMood: Mood) => {
    if (!wave) return;
    if (retuneTimer.current !== null) window.clearTimeout(retuneTimer.current);
    retuneTimer.current = window.setTimeout(() => {
      patchWave.mutate(
        { queueId: wave.queueId, temperature: nextTemperature, mood: nextMood },
        { onSuccess: applyWave },
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

  const qualified = status.data?.qualifiedSessions ?? 0;
  const learningLabel =
    qualified < 40
      ? `Collecting signal ${qualified}/40 qualified tracks`
      : `Baseline ${Math.min(qualified, 100)}/100 · model in shadow`;

  // While retuning, show what the slider promises rather than a stale mix.
  const shownMix = patchWave.isPending
    ? expectedFamiliarPercent(temperature)
    : (wave?.mix.actualFamiliarPercent ?? expectedFamiliarPercent(temperature));

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
            <div className={styles.temperatureLabels}>
              <span>Familiar</span>
              <span className={styles.mixValue}>
                {describeMix(shownMix)}
                {patchWave.isPending && <span className={styles.retuning}> · retuning…</span>}
              </span>
              <span>Discovery</span>
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
          </div>

          <Button variant="primary" onClick={startWave} disabled={createWave.isPending}>
            {createWave.isPending
              ? "Preparing…"
              : wave && playerState === "PLAYING"
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

        {wave && wave.relaxations.length > 0 && (
          <p className={styles.relaxation} role="status">
            {wave.relaxations.includes("FAMILIAR_POOL_WIDENED")
              ? "Not enough familiar tracks yet — filled the gap with discovery picks."
              : "Diversity widened to fill the queue."}
          </p>
        )}
      </Panel>

      <Panel title="Up next">
        {!wave && <EmptyState message="Start the Wave to build a queue from your local pool." />}
        {wave && (
          <ol className={styles.queue} aria-busy={patchWave.isPending}>
            {wave.items.map((item, index) => {
              const isCurrent = item.track.videoId === currentVideoId || index === currentIndex;
              return (
                <li
                  key={item.track.videoId}
                  className={isCurrent ? styles.currentItem : undefined}
                >
                  <button
                    type="button"
                    className={styles.queueRow}
                    onClick={() => void playIndex(index)}
                    aria-current={isCurrent ? "true" : undefined}
                  >
                    <span className={styles.position}>
                      {isCurrent && playerState === "PLAYING"
                        ? "▶"
                        : String(item.position).padStart(2, "0")}
                    </span>
                    <span className={styles.queueTitle}>{item.track.title}</span>
                    <span className={styles.queueArtist}>{item.track.artists.join(", ")}</span>
                    <span
                      className={
                        item.familiarity === "FAMILIAR" ? styles.familiar : styles.discovery
                      }
                    >
                      {item.familiarity === "FAMILIAR" ? "familiar" : "discovery"}
                    </span>
                    <span className={styles.reason}>
                      {item.reasonCodes[0] ? humanizeReason(item.reasonCodes[0]) : ""}
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
