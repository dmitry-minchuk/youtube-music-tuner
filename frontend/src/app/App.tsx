import { useEffect, useState } from "react";
import { CollectionPage } from "@/features/library/CollectionPage";
import { InsightsPage } from "@/features/insights/InsightsPage";
import { PlaylistsPage } from "@/features/playlists/PlaylistsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { WavePage } from "@/features/wave/WavePage";
import { PlayerBar } from "@/player/PlayerBar";
import { PlayerPanel } from "@/player/PlayerPanel";
import { Sidebar } from "@/app/Sidebar";
import { StatusIndicator } from "@/app/StatusIndicator";
import { routeFromHash, type RouteId } from "@/app/routes";
import styles from "@/app/App.module.css";

function useHashRoute(): RouteId {
  const [route, setRoute] = useState<RouteId>(() => routeFromHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

const PAGES: Record<RouteId, () => React.JSX.Element> = {
  wave: WavePage,
  collection: CollectionPage,
  playlists: PlaylistsPage,
  insights: InsightsPage,
  settings: SettingsPage,
};

export function App(): React.JSX.Element {
  const route = useHashRoute();
  const Page = PAGES[route];

  return (
    <div className={styles.shell}>
      <Sidebar active={route} />
      <div className={styles.body}>
        <div className={styles.workspace}>
          <main className={styles.main} id="main-content">
            <header className={styles.topbar}>
              <StatusIndicator />
            </header>
            <div className={styles.content}>
              <Page />
            </div>
          </main>
          <PlayerPanel />
        </div>
        <PlayerBar />
      </div>
    </div>
  );
}
