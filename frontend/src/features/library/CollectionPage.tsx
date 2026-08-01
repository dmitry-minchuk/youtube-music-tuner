import { useState } from "react";
import { formatDuration, useLibraryTracks, type LibraryView } from "@/api/library";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import { Button } from "@/ui/Button";
import styles from "@/features/library/CollectionPage.module.css";

const TABS: { id: LibraryView; label: string }[] = [
  { id: "liked", label: "Liked" },
  { id: "recent", label: "Recently played" },
  { id: "discovered", label: "Discovered" },
  { id: "blocked", label: "Blocked" },
];

export function CollectionPage(): React.JSX.Element {
  const [view, setView] = useState<LibraryView>("liked");
  const [filter, setFilter] = useState("");
  const { data, isLoading, isError } = useLibraryTracks(view);

  const items = (data?.items ?? []).filter((track) => {
    if (!filter.trim()) return true;
    const needle = filter.toLowerCase();
    return (
      track.title.toLowerCase().includes(needle) ||
      track.artists.some((artist) => artist.toLowerCase().includes(needle))
    );
  });

  return (
    <>
      <PageHeading title="Collection" subtitle="Everything Tuner knows locally" />

      <div className={styles.controls}>
        <div className={styles.tabs} role="tablist" aria-label="Collection views">
          {TABS.map((tab) => (
            <Button
              key={tab.id}
              role="tab"
              aria-selected={view === tab.id}
              variant={view === tab.id ? "primary" : "secondary"}
              onClick={() => setView(tab.id)}
            >
              {tab.label}
            </Button>
          ))}
        </div>
        <label className={styles.search}>
          <span className="visually-hidden">Filter the local catalogue</span>
          <input
            type="search"
            value={filter}
            placeholder="Filter locally…"
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
      </div>

      <Panel>
        {isLoading && <p className={styles.muted}>Reading the local catalogue…</p>}
        {isError && <p className={styles.muted}>The local API is unreachable.</p>}
        {data && items.length === 0 && (
          <EmptyState
            message={
              view === "liked"
                ? "No liked tracks cached yet. Connect YouTube Music and run the first sync from Settings."
                : "Nothing here yet."
            }
          />
        )}
        {items.length > 0 && (
          <table className={styles.table}>
            <caption className="visually-hidden">
              {items.length} of {data?.total ?? 0} tracks
            </caption>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Artist</th>
                <th scope="col">Album</th>
                <th scope="col" className={styles.numeric}>
                  Length
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((track) => (
                <tr key={track.videoId}>
                  <td>{track.title}</td>
                  <td>{track.artists.join(", ") || "—"}</td>
                  <td>{track.albumTitle ?? "—"}</td>
                  <td className={styles.numeric}>{formatDuration(track.durationSeconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
