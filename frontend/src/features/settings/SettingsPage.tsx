import { ApiError } from "@/api/client";
import { useAuthStatus, useSystemStatus } from "@/api/hooks";
import { useStartSync, useSyncStatus } from "@/api/library";
import { useSettings, useUpdateSettings } from "@/api/settings";
import { ConnectPanel } from "@/features/settings/ConnectPanel";
import { PageHeading, Panel } from "@/ui/Panel";
import { Button } from "@/ui/Button";
import styles from "@/features/settings/SettingsPage.module.css";

function formatTime(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleString();
}

function syncErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "LOCAL_BUDGET_EXCEEDED") {
      const next = error.body.nextAllowedAt;
      return typeof next === "string"
        ? `Sync is still within its 6 hour window — next allowed ${formatTime(next)}.`
        : "Sync budget for today is used up.";
    }
    if (error.code === "YTM_AUTH_REQUIRED") return "YouTube Music sync paused — reconnect required.";
    if (error.code === "CIRCUIT_OPEN") return "External calls are paused after repeated failures.";
    if (error.code === "JOB_ALREADY_RUNNING") return "A sync is already running.";
    return error.message;
  }
  return "Could not reach the local API.";
}

export function SettingsPage(): React.JSX.Element {
  const status = useSystemStatus();
  const auth = useAuthStatus();
  const sync = useSyncStatus();
  const startSync = useStartSync();
  const settings = useSettings();
  const updateSettings = useUpdateSettings();

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
          <div className={styles.connect}>
            <ConnectPanel auth={auth.data} />
          </div>
        </Panel>

        <Panel
          title="Library sync"
          description="Reads likes, playlists and available history at most every 6 hours"
          actions={
            <Button
              variant="primary"
              disabled={startSync.isPending || !auth.data?.connected}
              onClick={() => startSync.mutate()}
            >
              {startSync.isPending ? "Queuing…" : "Sync now"}
            </Button>
          }
        >
          <dl className={styles.definitions}>
            <div>
              <dt>Last successful</dt>
              <dd>{formatTime(sync.data?.lastSuccessfulSyncAt ?? null)}</dd>
            </div>
            <div>
              <dt>Next allowed</dt>
              <dd>{formatTime(sync.data?.nextAllowedAt ?? null)}</dd>
            </div>
            <div>
              <dt>Cached likes</dt>
              <dd>{sync.data?.likedCount ?? 0}</dd>
            </div>
            <div>
              <dt>Cached playlists</dt>
              <dd>{sync.data?.playlistCount ?? 0}</dd>
            </div>
          </dl>
          {startSync.isError && (
            <p className={styles.warn} role="status">
              {syncErrorMessage(startSync.error)}
            </p>
          )}
          {sync.data?.activeJob && (
            <p className={styles.muted} role="status">
              Sync job {sync.data.activeJob.status.toLowerCase()}…
            </p>
          )}
        </Panel>

        <Panel title="Playback" description="How the player behaves while you are elsewhere">
          <label className={styles.toggleRow}>
            <input
              type="checkbox"
              checked={settings.data?.pauseOnHidden ?? true}
              onChange={(event) =>
                updateSettings.mutate({ pauseOnHidden: event.target.checked })
              }
            />
            <span>
              <span className={styles.toggleTitle}>Pause when this tab is hidden</span>
              <span className={styles.toggleHint}>
                YouTube's Developer Policies treat a player outside the page you are viewing as a
                background player, so this is on by default. Browsers also report "hidden" when the
                window is simply covered by another application, which is stricter than the policy
                requires — switch it off if that gets in your way.
              </span>
            </span>
          </label>

          <label className={styles.toggleRow}>
            <span className={styles.toggleTitle}>Default temperature</span>
            <input
              type="range"
              min={0}
              max={100}
              value={settings.data?.defaultTemperature ?? 50}
              onChange={(event) =>
                updateSettings.mutate({ defaultTemperature: Number(event.target.value) })
              }
              aria-valuetext={`${settings.data?.defaultTemperature ?? 50}`}
            />
            <span className={styles.toggleValue}>{settings.data?.defaultTemperature ?? 50}</span>
          </label>
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
