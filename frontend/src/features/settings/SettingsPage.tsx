import { useAuthStatus, useSystemStatus } from "@/api/hooks";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/settings/SettingsPage.module.css";

function formatTime(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleString();
}

export function SettingsPage(): React.JSX.Element {
  const status = useSystemStatus();
  const auth = useAuthStatus();

  return (
    <>
      <PageHeading title="Settings" subtitle="Connection, automation and privacy" />

      <div className={styles.stack}>
        <Panel title="YouTube Music" description="Connection state and last synchronisation">
          {auth.isLoading && <p className={styles.muted}>Loading…</p>}
          {auth.data && (
            <dl className={styles.definitions}>
              <div>
                <dt>Status</dt>
                <dd>{auth.data.connected ? "Connected" : "Not connected"}</dd>
              </div>
              <div>
                <dt>Reason</dt>
                <dd>{auth.data.reason}</dd>
              </div>
              <div>
                <dt>Client credentials</dt>
                <dd>{auth.data.clientConfigured ? "imported" : "missing"}</dd>
              </div>
              <div>
                <dt>Last sync</dt>
                <dd>{formatTime(status.data?.lastSuccessfulSyncAt ?? null)}</dd>
              </div>
            </dl>
          )}
          {auth.data && !auth.data.connected && (
            <EmptyState
              message={
                "Connect from the terminal — the browser never receives the client secret:\n" +
                "docker compose exec -T tuner python -m app.cli credentials import - < client_secret.json\n" +
                "docker compose exec tuner python -m app.cli auth"
              }
            />
          )}
        </Panel>

        <Panel title="Diagnostics" description="Local health and external call budget">
          {status.data && (
            <dl className={styles.definitions}>
              <div>
                <dt>App version</dt>
                <dd>{status.data.appVersion}</dd>
              </div>
              <div>
                <dt>Port</dt>
                <dd>{status.data.publicPort}</dd>
              </div>
              <div>
                <dt>Auto publish</dt>
                <dd>{status.data.autoPublish ? "enabled" : "disabled"}</dd>
              </div>
              <div>
                <dt>External calls (24h)</dt>
                <dd>{status.data.externalCallsLast24h}</dd>
              </div>
              <div>
                <dt>Pending jobs</dt>
                <dd>{status.data.pendingJobs}</dd>
              </div>
              <div>
                <dt>Qualified listens</dt>
                <dd>{status.data.qualifiedSessions}</dd>
              </div>
            </dl>
          )}
        </Panel>
      </div>
    </>
  );
}
