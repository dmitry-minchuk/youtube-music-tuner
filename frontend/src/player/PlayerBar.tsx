import { useSetRating } from "@/api/library";
import { formatDuration } from "@/api/library";
import { currentTrack, usePlayerStore } from "@/player/playerStore";
import styles from "@/player/PlayerBar.module.css";

/**
 * Our own transport controls. They sit below the iframe and never mask or
 * imitate the player's required elements (docs/04 section 1).
 */
export function PlayerBar(): React.JSX.Element | null {
  const track = usePlayerStore(currentTrack);
  const state = usePlayerStore((store) => store.state);
  const position = usePlayerStore((store) => store.positionSeconds);
  const duration = usePlayerStore((store) => store.durationSeconds);
  const volume = usePlayerStore((store) => store.volume);
  const pending = usePlayerStore((store) => store.pendingEvents);
  const setRating = useSetRating();

  if (!track) return null;

  const store = usePlayerStore.getState();
  const isPlaying = state === "PLAYING";

  const rate = (desiredState: "LIKE" | "DISLIKE") => {
    void store.rate(desiredState);
    setRating.mutate({ videoId: track.videoId, desiredState });
  };

  return (
    <div className={styles.bar}>
      <div className={styles.info}>
        <span className={styles.title}>{track.title}</span>
        <span className={styles.artist}>{track.artists.join(", ")}</span>
      </div>

      <div className={styles.controls}>
        <button type="button" onClick={() => void store.previous()} aria-label="Previous track">
          ◀◀
        </button>
        <button
          type="button"
          className={styles.primary}
          onClick={() => store.togglePlay()}
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? "❙❙" : "▶"}
        </button>
        <button type="button" onClick={() => void store.next()} aria-label="Next track">
          ▶▶
        </button>
      </div>

      <div className={styles.progress}>
        <span className={styles.time}>{formatDuration(Math.floor(position))}</span>
        <input
          type="range"
          min={0}
          max={Math.max(1, Math.floor(duration ?? 0))}
          value={Math.floor(position)}
          onChange={(event) => store.seekTo(Number(event.target.value))}
          aria-label="Seek"
          aria-valuetext={`${formatDuration(Math.floor(position))} of ${formatDuration(
            duration === null ? null : Math.floor(duration),
          )}`}
        />
        <span className={styles.time}>
          {formatDuration(duration === null ? null : Math.floor(duration))}
        </span>
      </div>

      <div className={styles.ratings}>
        <button type="button" onClick={() => rate("LIKE")} aria-label="Like this track">
          ♡ Like
        </button>
        <button type="button" onClick={() => rate("DISLIKE")} aria-label="Dislike this track">
          ⊘ Dislike
        </button>
      </div>

      <label className={styles.volume}>
        <span className="visually-hidden">Volume</span>
        <input
          type="range"
          min={0}
          max={100}
          value={volume}
          onChange={(event) => store.setVolume(Number(event.target.value))}
          aria-valuetext={`Volume ${volume} percent`}
        />
      </label>

      {pending > 0 && (
        <span className={styles.pending} title="Events waiting to be delivered">
          {pending} pending
        </span>
      )}
    </div>
  );
}
