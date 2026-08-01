import { formatDuration, useSetRating, useTrackRating } from "@/api/library";
import { currentTrack, usePlayerStore } from "@/player/playerStore";
import { Icon } from "@/ui/Icon";
import styles from "@/player/PlayerBar.module.css";

import type { RatingSyncStatus } from "@/api/library";

const SYNC_LABEL: Record<RatingSyncStatus, string> = {
  PENDING: "syncing…",
  SYNCED: "synced",
  FAILED: "not synced",
};

/**
 * Our own transport controls. They sit below the iframe and never mask or
 * imitate the player's required elements (docs/04 section 1).
 *
 * The rating buttons show their state: a click has to be visible, and the
 * sync status has to be honest about whether YouTube Music took it
 * (docs/03 section 6, docs/06 section 10).
 */
export function PlayerBar(): React.JSX.Element | null {
  const track = usePlayerStore(currentTrack);
  const state = usePlayerStore((store) => store.state);
  const position = usePlayerStore((store) => store.positionSeconds);
  const duration = usePlayerStore((store) => store.durationSeconds);
  const volume = usePlayerStore((store) => store.volume);
  const pending = usePlayerStore((store) => store.pendingEvents);
  const ready = usePlayerStore((store) => store.port !== null);

  const rating = useTrackRating(track?.videoId ?? null);
  const setRating = useSetRating();

  if (!track) return null;

  const store = usePlayerStore.getState();
  const isPlaying = state === "PLAYING";
  const buffering = state === "BUFFERING";
  const liked = rating.data?.desiredState === "LIKE";
  const disliked = rating.data?.desiredState === "DISLIKE";
  const syncStatus = rating.data?.syncStatus;

  // Clicking an active rating clears it, like every other player does.
  const rate = (target: "LIKE" | "DISLIKE") => {
    const active = target === "LIKE" ? liked : disliked;
    const desiredState = active ? "INDIFFERENT" : target;
    void store.rate(target);
    setRating.mutate({ videoId: track.videoId, desiredState });
  };

  return (
    <div className={styles.bar}>
      <div className={styles.info}>
        <span className={styles.title}>{track.title}</span>
        <span className={styles.artist}>{track.artists.join(", ")}</span>
      </div>

      <div className={styles.centre}>
        <div className={styles.controls}>
          <button
            type="button"
            onClick={() => void store.previous()}
            aria-label="Previous track"
            disabled={!ready}
          >
            <Icon name="previous" />
          </button>
          <button
            type="button"
            className={styles.primary}
            onClick={() => store.togglePlay()}
            aria-label={isPlaying ? "Pause" : "Play"}
            disabled={!ready}
            title={ready ? undefined : "Player is still loading"}
          >
            {buffering ? "…" : <Icon name={isPlaying ? "pause" : "play"} />}
          </button>
          <button
            type="button"
            onClick={() => void store.next()}
            aria-label="Next track"
            disabled={!ready}
          >
            <Icon name="next" />
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
      </div>

      <div className={styles.right}>
        <div className={styles.ratings}>
          <button
            type="button"
            className={liked ? `${styles.rateButton} ${styles.liked}` : styles.rateButton}
            onClick={() => rate("LIKE")}
            aria-pressed={liked}
            aria-label={liked ? "Remove like" : "Like this track"}
          >
            <Icon name="heart" size={17} />
            Like
          </button>
          <button
            type="button"
            className={disliked ? `${styles.rateButton} ${styles.disliked}` : styles.rateButton}
            onClick={() => rate("DISLIKE")}
            aria-pressed={disliked}
            aria-label={disliked ? "Remove dislike" : "Dislike this track"}
          >
            <Icon name="dislike" size={17} />
            Dislike
          </button>
          {(liked || disliked) && syncStatus && (
            <span
              className={syncStatus === "FAILED" ? styles.syncFailed : styles.sync}
              role="status"
            >
              {SYNC_LABEL[syncStatus]}
            </span>
          )}
        </div>

        <label className={styles.volume} title={`Volume ${volume}%`}>
          <span className="visually-hidden">Volume</span>
          <Icon name="volume" size={17} />
          <input
            type="range"
            min={0}
            max={100}
            value={volume}
            onChange={(event) => store.setVolume(Number(event.target.value))}
            aria-valuetext={`Volume ${volume} percent`}
          />
          <span className={styles.volumeValue}>{volume}%</span>
        </label>

        {pending > 0 && (
          <span className={styles.pending} title="Events waiting to be delivered">
            {pending} pending
          </span>
        )}
      </div>
    </div>
  );
}
