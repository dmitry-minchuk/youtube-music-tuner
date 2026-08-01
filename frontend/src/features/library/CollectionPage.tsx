import { PageHeading, Panel } from "@/ui/Panel";

export function CollectionPage(): React.JSX.Element {
  return (
    <>
      <PageHeading title="Collection" subtitle="Liked and locally known tracks" />
      <Panel>
        <p>The local catalogue appears after the first library sync.</p>
      </Panel>
    </>
  );
}
