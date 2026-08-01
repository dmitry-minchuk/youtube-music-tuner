import { PageHeading, Panel } from "@/ui/Panel";

export function WavePage(): React.JSX.Element {
  return (
    <>
      <PageHeading title="Your Wave" subtitle="Pick a mood and how far to explore" />
      <Panel>
        <p>The wave queue arrives with the recommender (phase 3).</p>
      </Panel>
    </>
  );
}
