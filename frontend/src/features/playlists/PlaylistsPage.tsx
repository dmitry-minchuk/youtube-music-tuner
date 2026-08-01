import { PageHeading, Panel } from "@/ui/Panel";

export function PlaylistsPage(): React.JSX.Element {
  return (
    <>
      <PageHeading title="Playlists" subtitle="Tuner playlists and your YouTube Music library" />
      <Panel>
        <p>Managed playlists arrive with safe publishing (phase 5).</p>
      </Panel>
    </>
  );
}
