import { useEffect, useRef, useState } from "react";
import { useSettings } from "@/api/settings";
import { createIframePlayer } from "@/player/iframeAdapter";
import { currentTrack, usePlayerStore } from "@/player/playerStore";
import styles from "@/player/PlayerPanel.module.css";

/**
 * Hosts the official IFrame player. It stays visible, is never covered and
 * keeps at least 200x200 px (docs/04 section 1).
 */
export function PlayerPanel(): React.JSX.Element {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const attachPort = usePlayerStore((state) => state.attachPort);
  const detachPort = usePlayerStore((state) => state.detachPort);
  const pausedByPolicy = usePlayerStore((state) => state.pausedByPolicy);
  const track = usePlayerStore(currentTrack);
  const settings = useSettings();
  const setPauseOnHidden = usePlayerStore((state) => state.setPauseOnHidden);

  useEffect(() => {
    let disposed = false;
    const mount = container.current;
    if (!mount) return;

    createIframePlayer({ container: mount, origin: window.location.origin })
      .then((port) => {
        if (disposed) {
          port.destroy();
          return;
        }
        attachPort(port);
      })
      .catch(() => setError("The YouTube player could not be loaded."));

    return () => {
      disposed = true;
      detachPort();
    };
  }, [attachPort, detachPort]);

  useEffect(() => {
    if (settings.data) setPauseOnHidden(settings.data.pauseOnHidden);
  }, [settings.data, setPauseOnHidden]);

  useEffect(() => {
    const onVisibility = () =>
      usePlayerStore.getState().handleVisibilityChange(document.visibilityState === "hidden");
    const onPageHide = () => usePlayerStore.getState().handlePageHide();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, []);

  return (
    <aside className={styles.panel} aria-label="Now playing">
      <h2 className={styles.heading}>Now playing</h2>
      <div className={styles.frame}>
        <div ref={container} className={styles.mount} />
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {pausedByPolicy && (
        <p className={styles.notice} role="status">
          Playback paused because this tab is no longer visible. You can turn this off in
          Settings → Playback.
        </p>
      )}
      {track ? (
        <div className={styles.meta}>
          <p className={styles.title}>{track.title}</p>
          <p className={styles.artist}>{track.artists.join(", ")}</p>
          {track.reasonCodes && track.reasonCodes.length > 0 && (
            <p className={styles.reason}>{humanizeReason(track.reasonCodes[0]!)}</p>
          )}
        </div>
      ) : (
        <p className={styles.meta}>Nothing queued yet.</p>
      )}
    </aside>
  );
}

const REASON_TEXT: Record<string, string> = {
  LIKED_TRACK: "From your likes",
  LIKED_ARTIST_NEW_TRACK: "Liked artist, new track",
  RELATED_TO_POSITIVE_SEED: "Related to something you finished",
  ARTIST_NOT_RECENT: "You have not heard this artist lately",
  REDISCOVERY: "Not played for a long time",
  HIGH_UNCERTAINTY: "Discovery pick with high uncertainty",
  RADIO_SOURCE: "From the radio around a track you liked",
  MOOD_MATCH: "Matches the chosen context",
};

export function humanizeReason(code: string): string {
  return REASON_TEXT[code] ?? code.toLowerCase().replaceAll("_", " ");
}
