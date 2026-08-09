import { formatDuration, useSetRating, useTrackRating, useVeto } from "@/api/library";
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
  const setVeto = useVeto();

  if (!track) return null;

  const store = usePlayerStore.getState();
  const isPlaying = state === "PLAYING";
  const buffering = state === "BUFFERING";
  const liked = rating.data?.desiredState === "LIKE";
  const disliked = rating.data?.desiredState === "DISLIKE";
  const vetoed = rating.data?.vetoed === true;
  const syncStatus = rating.data?.syncStatus;

  // Clicking an active rating clears it, like every other player does.
  // Setting a dislike also moves on — staying on a rejected track makes no
  // sense — and lets the queue tail adapt to the signal (docs/04 s.8).
  const rate = (target: "LIKE" | "DISLIKE") => {
    const active = target === "LIKE" ? liked : disliked;
    const desiredState = active ? "INDIFFERENT" : target;
    if (desiredState === "DISLIKE") void store.dislikeCurrent();
    else void store.rate(target);
    setRating.mutate({ videoId: track.videoId, desiredState });
  };

  // "Don't Like At All": local-only, stronger than a dislike — the track,
  // its artist and its graph neighbourhood step back (docs/05 s.11).
  // Setting it also skips ahead; removing it just clears the flag.
  const toggleVeto = () => {
    const next = !vetoed;
    setVeto.mutate({ videoId: track.videoId, vetoed: next });
    if (next) void store.vetoCurrent();
  };

  return (
    <div className={styles.bar}>
      <div className={styles.info}>
        <span className={styles.title}>{track.title}</span>
        <span className={styles.artist}>{track.artists.join(", ")}</span>
      </div>

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
          {buffering ? (
            <span className={styles.buffering}>…</span>
          ) : (
            <Icon name={isPlaying ? "pause" : "play"} />
          )}
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
        <button
          type="button"
          className={
            vetoed
              ? `${styles.rateButton} ${styles.veto} ${styles.vetoActive}`
              : `${styles.rateButton} ${styles.veto}`
          }
          onClick={toggleVeto}
          aria-pressed={vetoed}
          aria-label={
            vetoed
              ? "Remove 'not my thing'"
              : "Not my thing — push this track, its artist and similar picks away"
          }
          title="Stronger than a dislike: also pushes away the artist and similar picks. Stays on this machine."
        >
          <Icon name="ban" size={17} />
          Not my thing
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

      <div className={styles.utility}>
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
