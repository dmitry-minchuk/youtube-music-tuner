import { useState } from "react";
import {
  formatPercent,
  useApiBudget,
  useInsightsSummary,
  useLearningStatus,
} from "@/api/insights";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/insights/InsightsPage.module.css";

export function InsightsPage(): React.JSX.Element {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("30d");
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const learning = useLearningStatus();
  const summary = useInsightsSummary(period);
  const budget = useApiBudget();

  return (
    <>
      <PageHeading title="Insights" subtitle="What Tuner learned from your listening" />

      <div className={styles.stack}>
        <Panel title="Learning">
          {learning.data && (
            <>
              <p className={styles.headline}>{learning.data.label}</p>
              <dl className={styles.definitions}>
                <div>
                  <dt>Serving policy</dt>
                  <dd>{learning.data.servingPolicy}</dd>
                </div>
                <div>
                  <dt>Qualified listens</dt>
                  <dd>{learning.data.qualifiedSessions}</dd>
                </div>
                <div>
                  <dt>Positive / negative</dt>
                  <dd>
                    {learning.data.positiveSessions} / {learning.data.negativeSessions}
                  </dd>
                </div>
                <div>
                  <dt>Distinct tracks</dt>
                  <dd>{learning.data.distinctTracks}</dd>
                </div>
              </dl>
              {learning.data.phase === "SHADOW" && (
                <p className={styles.note}>
                  The model is learning alongside the queue but does not order it yet — the first
                  {" "}
                  {learning.data.thresholds.cleanBaseline} listens stay on the fixed baseline
                  ranker.
                </p>
              )}
            </>
          )}
        </Panel>

        <Panel
          title="Listening"
          description="Rates use tracks with a known duration, so the comparison stays fair"
          actions={
            <select
              value={period}
              onChange={(event) => setPeriod(event.target.value as "7d" | "30d" | "90d")}
              aria-label="Period"
              className={styles.select}
            >
              <option value="7d">7 days</option>
              <option value="30d">30 days</option>
              <option value="90d">90 days</option>
            </select>
          }
        >
          {summary.data && summary.data.sampleSize === 0 && (
            <EmptyState message="No qualified listens in this period yet." />
          )}
          {summary.data && summary.data.sampleSize > 0 && (
            <>
              <dl className={styles.definitions}>
                <div>
                  <dt>Early skips</dt>
                  <dd>{formatPercent(summary.data.earlySkipRate)}</dd>
                </div>
                <div>
                  <dt>Finished</dt>
                  <dd>{formatPercent(summary.data.completionRate)}</dd>
                </div>
                <div>
                  <dt>Familiar / discovery played</dt>
                  <dd>
                    {summary.data.playedFamiliar} / {summary.data.playedDiscovery}
                  </dd>
                </div>
                <div>
                  <dt>Sample size</dt>
                  <dd>{summary.data.sampleSize} listens</dd>
                </div>
              </dl>
              {summary.data.absoluteTimeSessions > 0 && (
                <p className={styles.note}>
                  {summary.data.absoluteTimeSessions} listen(s) had no duration and are counted
                  separately.
                </p>
              )}
              {summary.data.topPositiveArtists.length > 0 && (
                <p className={styles.note}>
                  Most finished:{" "}
                  {summary.data.topPositiveArtists.map((row) => row.artist).join(", ")}
                </p>
              )}
            </>
          )}
        </Panel>

        <Panel
          title="Diagnostics"
          actions={
            <button
              type="button"
              className={styles.toggle}
              onClick={() => setShowDiagnostics((value) => !value)}
              aria-expanded={showDiagnostics}
            >
              {showDiagnostics ? "Hide" : "Show"}
            </button>
          }
        >
          {!showDiagnostics && (
            <p className={styles.note}>Model version, event counts and the call ledger.</p>
          )}
          {showDiagnostics && budget.data && (
            <dl className={styles.definitions}>
              <div>
                <dt>Library sync today</dt>
                <dd>
                  {budget.data.librarySync.used} / {budget.data.librarySync.limit}
                </dd>
              </div>
              <div>
                <dt>Playlist requests today</dt>
                <dd>
                  {budget.data.playlistRequests.used} / {budget.data.playlistRequests.limit}
                </dd>
              </div>
              <div>
                <dt>External calls (24h)</dt>
                <dd>{budget.data.externalCallsLast24h}</dd>
              </div>
              <div>
                <dt>Circuit</dt>
                <dd>{budget.data.circuit.open ? `open (${budget.data.circuit.reason})` : "closed"}</dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{learning.data?.servingModelId ?? learning.data?.shadowModelId ?? "none"}</dd>
              </div>
              <div>
                <dt>Last training</dt>
                <dd>
                  {summary.data?.lastTrainingAt
                    ? new Date(summary.data.lastTrainingAt).toLocaleString()
                    : "never"}
                </dd>
              </div>
            </dl>
          )}
        </Panel>
      </div>
    </>
  );
}
