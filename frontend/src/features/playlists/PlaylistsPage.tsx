import { useState } from "react";
import { ApiError } from "@/api/client";
import { usePlaylists, type TunerPlaylistDto } from "@/api/library";
import {
  describeGate,
  useDeleteSetupArtifact,
  usePlanPlaylist,
  usePublishPlaylist,
  useReconcilePlaylist,
  useSetupPlaylists,
  type Kind,
  type PlanResponse,
} from "@/api/publishing";
import { Button } from "@/ui/Button";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/playlists/PlaylistsPage.module.css";

const KIND_LABELS: Record<Kind, string> = {
  FAMILIAR: "Tuner · Familiar",
  BALANCE: "Tuner · Balance",
  DISCOVERY: "Tuner · Discovery",
};

const NON_ACTIVE_HINT: Record<string, string> = {
  CREATING: "Setup started, but no remote playlist is confirmed yet.",
  UNVERIFIED: "Created remotely but not verified. Verify it or delete it before publishing.",
  CLEANUP_REQUIRED: "Several playlists match the marker. Resolve this before publishing.",
  DELETED: "The setup artifact was removed.",
};

function publishError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "LOCAL_BUDGET_EXCEEDED") {
      const next = error.body.nextAllowedAt;
      return typeof next === "string"
        ? `The daily publish window opens at ${new Date(next).toLocaleString()}.`
        : "The publish budget for today is used up.";
    }
    if (error.code === "PLAYLIST_QUALITY_FAILED") {
      const failures = (error.body.gateFailures as string[] | undefined) ?? [];
      return `Quality gates blocked this publish: ${failures.map(describeGate).join("; ")}.`;
    }
    if (error.code === "PLAYLIST_UNVERIFIED") return "Verify the playlist before publishing.";
    if (error.code === "PLAYLIST_NOT_MANAGED") return "Tuner does not own that playlist.";
    if (error.code === "REMOTE_CHANGED") return "The playlist changed remotely; nothing was written.";
    return error.message;
  }
  return "Could not reach the local API.";
}

function PlanSummary({ plan }: { plan: PlanResponse }): React.JSX.Element {
  if (plan.status === "READY") {
    return (
      <p className={styles.meta}>
        {plan.effectiveTargetSize === plan.configuredTargetSize
          ? `${plan.effectiveTargetSize} tracks ready to publish.`
          : `${plan.effectiveTargetSize} of configured ${plan.configuredTargetSize} · limited by ${plan.availableFamiliar} familiar tracks.`}
      </p>
    );
  }
  return (
    <div className={styles.warnBlock}>
      <p className={styles.warn}>
        {plan.reasonCode === "INSUFFICIENT_POOL"
          ? `Not enough tracks yet: ${plan.availableFamiliar} familiar and ${plan.availableDiscovery} discovery available, ${plan.requiredFamiliar} and ${plan.requiredDiscovery} needed for the minimum of ${plan.minimumPublishSize}.`
          : plan.gateFailures.map(describeGate).join("; ")}
      </p>
      <p className={styles.meta}>
        Lower the temperature, reduce the configured size, or collect more likes and listens.
      </p>
    </div>
  );
}

function TunerPlaylistCard({ playlist }: { playlist: TunerPlaylistDto }): React.JSX.Element {
  const kind = playlist.kind;
  const plan = usePlanPlaylist();
  const publish = usePublishPlaylist();
  const reconcile = useReconcilePlaylist();
  const remove = useDeleteSetupArtifact();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const isActive = playlist.status === "ACTIVE";

  return (
    <li className={styles.card}>
      <div className={styles.cardHead}>
        <h3>{KIND_LABELS[kind]}</h3>
        <span className={isActive ? styles.badge : `${styles.badge} ${styles.badgeWarn}`}>
          {playlist.status}
        </span>
      </div>

      <p className={styles.meta}>
        Temperature {playlist.temperature} · target {playlist.configuredTargetSize}
      </p>
      <p className={styles.meta}>
        {playlist.lastPublishedAt
          ? `Last publish ${new Date(playlist.lastPublishedAt).toLocaleString()}`
          : "Never published"}
      </p>

      {!isActive && <p className={styles.warn}>{NON_ACTIVE_HINT[playlist.status]}</p>}
      {plan.data && <PlanSummary plan={plan.data} />}

      {publish.data && (
        <p className={styles.meta}>
          {publish.data.status === "COMPLETE"
            ? "Published and verified."
            : publish.data.status === "PARTIAL"
              ? `Partially applied — ${publish.data.remainingItemChanges} changes and ${publish.data.remainingEstimatedRequests} requests remain; continues after ${
                  publish.data.nextContinuationAfter
                    ? new Date(publish.data.nextContinuationAfter).toLocaleString()
                    : "the next window"
                }.`
              : `Publish ${publish.data.status.toLowerCase()}${
                  publish.data.errorCode ? ` (${publish.data.errorCode})` : ""
                }.`}
        </p>
      )}
      {publish.isError && <p className={styles.warn}>{publishError(publish.error)}</p>}

      <div className={styles.actions}>
        <Button onClick={() => plan.mutate(kind)} disabled={plan.isPending}>
          {plan.isPending ? "Checking…" : "Preview"}
        </Button>
        {isActive ? (
          <Button
            variant="primary"
            onClick={() => publish.mutate(kind)}
            disabled={publish.isPending}
          >
            {publish.isPending ? "Publishing…" : "Publish now"}
          </Button>
        ) : (
          <>
            <Button onClick={() => reconcile.mutate(kind)} disabled={reconcile.isPending}>
              Verify / adopt
            </Button>
            {confirmingDelete ? (
              <Button
                variant="danger"
                onClick={() => {
                  remove.mutate(kind);
                  setConfirmingDelete(false);
                }}
              >
                {`Delete remote playlist ${playlist.playlistId ?? "(none)"} — confirm`}
              </Button>
            ) : (
              <Button variant="danger" onClick={() => setConfirmingDelete(true)}>
                Delete unverified
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function PlaylistsPage(): React.JSX.Element {
  const { data, isLoading } = usePlaylists();
  const setup = useSetupPlaylists();

  return (
    <>
      <PageHeading
        title="Playlists"
        subtitle="Tuner writes to its own three playlists; yours stay read-only"
      />

      <div className={styles.stack}>
        <Panel
          title="Tuner playlists"
          description="Private, marker-verified and never touched without a preview"
          actions={
            data && data.tunerPlaylists.length === 0 ? (
              <Button
                variant="primary"
                onClick={() => setup.mutate()}
                disabled={setup.isPending}
              >
                {setup.isPending ? "Creating…" : "Create the three playlists"}
              </Button>
            ) : undefined
          }
        >
          {isLoading && <p className={styles.meta}>Loading…</p>}
          {data && data.tunerPlaylists.length === 0 && (
            <EmptyState message="Not created yet. Preview first, then create them in one confirmed step." />
          )}
          {setup.isError && <p className={styles.warn}>{publishError(setup.error)}</p>}
          {data && data.tunerPlaylists.length > 0 && (
            <ul className={styles.cards}>
              {data.tunerPlaylists.map((playlist) => (
                <TunerPlaylistCard key={playlist.managedPlaylistId} playlist={playlist} />
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
