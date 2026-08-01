import { usePlaylists } from "@/api/library";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/playlists/PlaylistsPage.module.css";

const KIND_LABELS = {
  FAMILIAR: "Tuner · Familiar",
  BALANCE: "Tuner · Balance",
  DISCOVERY: "Tuner · Discovery",
} as const;

/** CREATING/UNVERIFIED/CLEANUP_REQUIRED must never look like a ready playlist. */
const NON_ACTIVE_HINT: Record<string, string> = {
  CREATING: "Setup started but no remote playlist confirmed yet.",
  UNVERIFIED: "Created remotely, not verified yet. Verify or delete before publishing.",
  CLEANUP_REQUIRED: "Ambiguous setup state. Resolve it manually before publishing.",
};

export function PlaylistsPage(): React.JSX.Element {
  const { data, isLoading } = usePlaylists();

  return (
    <>
      <PageHeading
        title="Playlists"
        subtitle="Tuner keeps three private playlists; your own stay read-only"
      />

      <div className={styles.stack}>
        <Panel title="Tuner playlists" description="Only these are ever written to">
          {isLoading && <p className={styles.muted}>Loading…</p>}
          {data && data.tunerPlaylists.length === 0 && (
            <EmptyState message="Not created yet. They appear after the first setup preview." />
          )}
          {data && data.tunerPlaylists.length > 0 && (
            <ul className={styles.cards}>
              {data.tunerPlaylists.map((playlist) => (
                <li key={playlist.managedPlaylistId} className={styles.card}>
                  <div className={styles.cardHead}>
                    <h3>{KIND_LABELS[playlist.kind]}</h3>
                    <span
                      className={
                        playlist.status === "ACTIVE"
                          ? styles.badge
                          : `${styles.badge} ${styles.badgeWarn}`
                      }
                    >
                      {playlist.status}
                    </span>
                  </div>
                  <p className={styles.meta}>
                    Temperature {playlist.temperature} · target {playlist.configuredTargetSize}
                  </p>
                  {playlist.status !== "ACTIVE" && (
                    <p className={styles.warn}>{NON_ACTIVE_HINT[playlist.status]}</p>
                  )}
                  <p className={styles.meta}>
                    {playlist.lastPublishedAt
                      ? `Last publish ${new Date(playlist.lastPublishedAt).toLocaleString()}`
                      : "Never published"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="Your YouTube Music playlists"
          description="Read-only — Tuner never modifies these"
        >
          {data && data.remotePlaylists.length === 0 && (
            <EmptyState message="Nothing cached yet. Run a library sync from Settings." />
          )}
          {data && data.remotePlaylists.length > 0 && (
            <ul className={styles.cards}>
              {data.remotePlaylists.map((playlist) => (
                <li key={playlist.playlistId} className={styles.card}>
                  <h3>{playlist.title}</h3>
                  <p className={styles.meta}>{playlist.trackCount} tracks</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}
