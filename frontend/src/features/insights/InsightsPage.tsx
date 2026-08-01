import { useSystemStatus } from "@/api/hooks";
import { PageHeading, Panel } from "@/ui/Panel";

export function InsightsPage(): React.JSX.Element {
  const { data } = useSystemStatus();
  const qualified = data?.qualifiedSessions ?? 0;

  return (
    <>
      <PageHeading title="Insights" subtitle="What Tuner learned from your listening" />
      <Panel>
        <p>
          {qualified < 40
            ? `Collecting signal ${qualified}/40 qualified tracks`
            : `Baseline ${Math.min(qualified, 100)}/100 · model in shadow`}
        </p>
      </Panel>
    </>
  );
}
