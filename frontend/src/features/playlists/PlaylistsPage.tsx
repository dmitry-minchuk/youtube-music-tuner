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
  type PlanTrack,
  type SetupResult,
} from "@/api/publishing";
import { usePlayerStore } from "@/player/playerStore";
import { Busy } from "@/ui/Busy";
import { Button } from "@/ui/Button";
import { EmptyState, PageHeading, Panel } from "@/ui/Panel";
import styles from "@/features/playlists/PlaylistsPage.module.css";

const KIND_LABELS: Record<Kind, string> = {
  FAMILIAR: "Tuner · Familiar",
  BALANCE: "Tuner · Balance",
  DISCOVERY: "Tuner · Discovery",
};

const NON_ACTIVE_HINT: Record<string, string> = {
  CREATING:
    "No playlist exists on YouTube for this slot yet. Create it, or delete the leftover intent.",
  UNVERIFIED: "Created remotely but not verified. Verify it or delete it before publishing.",
  CLEANUP_REQUIRED: "Several playlists match the marker. Resolve this before publishing.",
};

/** What Verify/adopt actually achieved, in words (docs/06 s.6). */
function reconcileOutcome(status: string, playlistId: string | null): string {
  if (status === "ACTIVE") return "Verified — the playlist matches what was written.";
  if (status === "UNVERIFIED") return "Adopted a matching playlist, but its content differs.";
  if (status === "CLEANUP_REQUIRED") return "Several playlists carry our marker — resolve manually.";
  if (status === "CREATING" && !playlistId) {
    return "No matching playlist found on YouTube — create it or delete this slot.";
  }
  return `Status: ${status.toLowerCase()}.`;
}

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

/** YouTube omits the size of its own system playlists — that is not zero. */
function describeTrackCount(count: number | null): string {
  if (count === null) return "size not reported by YouTube";
  return count === 1 ? "1 track" : `${count} tracks`;
}

/** A setup that skipped a playlist still returns 200, so say what happened. */
function setupOutcome(result: SetupResult): string {
  if (result.alreadyExisting) return "already exists — left untouched.";
  if (result.status === "SKIPPED_QUALITY") {
    if (result.reasonCode === "INSUFFICIENT_POOL") {
      const familiar = result.availableFamiliar ?? 0;
      const discovery = result.availableDiscovery ?? 0;
      return (
        `not enough material yet (${familiar} familiar and ${discovery} discovery tracks pass ` +
        `the quality bar, ${result.minimumPublishSize ?? 25} needed). Listen and like a little ` +
        `more, then try again.`
      );
    }
    const failures = result.gateFailures ?? [];
    return failures.length
      ? `skipped by quality gates: ${failures.map(describeGate).join("; ")}.`
      : "skipped by quality gates.";
  }
  if (result.errorCode) return `failed (${result.errorCode}).`;
  const size = result.effectiveTargetSize;
  return size ? `created with ${size} tracks.` : `created (${result.status}).`;
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
  const setup = useSetupPlaylists();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const setQueue = usePlayerStore((store) => store.setQueue);
  const playIndex = usePlayerStore((store) => store.playIndex);

  const isActive = playlist.status === "ACTIVE";
  // An intent without a remote id has nothing to verify or adopt — the only
  // way forward is creating the playlist (setup is idempotent, docs/03 s.8).
  const needsCreation = playlist.status === "CREATING" && playlist.playlistId === null;
  const previewTracks = plan.data?.status === "READY" ? plan.data.tracks : [];

  // Anything that leaves the machine (or ranks 360 tracks) takes visible
  // time: a moving bar says "working", a frozen button says "hung".
  const busyLabel = plan.isPending
    ? "Building the preview — ranking the whole pool…"
    : publish.isPending
      ? "Publishing to YouTube…"
      : setup.isPending
        ? "Creating on YouTube and verifying…"
        : reconcile.isPending
          ? "Reading the playlist back from YouTube…"
          : remove.isPending
            ? "Deleting on YouTube…"
            : null;

  /** Load the reviewed list into the player so it can be listened to. */
  const playPreview = (tracks: PlanTrack[], from = 0) => {
    setQueue(
      tracks.map((track) => ({
        videoId: track.videoId,
        title: track.title,
        artists: track.artists,
        durationSeconds: null,
        reasonCodes: track.reasonCodes,
        familiarity: track.familiarity,
      })),
    );
    void playIndex(from);
  };

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

      {!isActive && (
        <p className={styles.warn}>
          {NON_ACTIVE_HINT[playlist.status]}
          {playlist.setupErrorCode ? ` Last error: ${playlist.setupErrorCode}.` : ""}
        </p>
      )}
      {plan.data && <PlanSummary plan={plan.data} />}
      {previewTracks.length > 0 && (
        <div className={styles.preview}>
          <div className={styles.previewHead}>
            <span className={styles.meta}>
              {previewTracks.length} tracks
              {plan.data?.generatedAt
                ? ` · reviewed ${new Date(plan.data.generatedAt).toLocaleTimeString()}`
                : ""}
            </span>
            <div className={styles.previewActions}>
              <Button onClick={() => playPreview(previewTracks)}>Play</Button>
              <Button
                onClick={() => plan.mutate({ kind, regenerate: true })}
                disabled={plan.isPending}
              >
                {plan.isPending ? "Rebuilding…" : "Regenerate"}
              </Button>
            </div>
          </div>
          <ol className={styles.trackList}>
            {previewTracks.map((track, index) => (
              <li key={track.videoId}>
                <button
                  type="button"
                  className={styles.trackRow}
                  onClick={() => playPreview(previewTracks, index)}
                  title="Play from here"
                >
                  <span className={styles.trackIndex}>{index + 1}</span>
                  <span className={styles.trackTitle}>{track.title}</span>
                  <span className={styles.trackArtist}>{track.artists.join(", ")}</span>
                  <span
                    className={
                      track.familiarity === "FAMILIAR"
                        ? styles.tagFamiliar
                        : styles.tagDiscovery
                    }
                  >
                    {track.familiarity === "FAMILIAR" ? "known" : "new"}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}

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

      {busyLabel && <Busy label={busyLabel} />}
      {reconcile.data && !busyLabel && (
        <p className={styles.meta} role="status">
          {reconcileOutcome(reconcile.data.status, reconcile.data.playlistId)}
          {reconcile.data.errorCode ? ` (${reconcile.data.errorCode})` : ""}
        </p>
      )}
      {reconcile.isError && <p className={styles.warn}>{publishError(reconcile.error)}</p>}
      {setup.isError && <p className={styles.warn}>{publishError(setup.error)}</p>}

      <div className={styles.actions}>
        <Button onClick={() => plan.mutate({ kind })} disabled={plan.isPending}>
          {plan.isPending ? "Checking…" : plan.data ? "Refresh preview" : "Preview"}
        </Button>
        {isActive && (
          <Button
            variant="primary"
            onClick={() => publish.mutate(kind)}
            disabled={publish.isPending || !plan.data}
            title={plan.data ? undefined : "Preview the list before publishing"}
          >
            {publish.isPending ? "Publishing…" : "Publish now"}
          </Button>
        )}
        {needsCreation && (
          <Button
            variant="primary"
            onClick={() => setup.mutate()}
            disabled={setup.isPending}
            title="Setup skips playlists that already exist"
          >
            {setup.isPending ? "Creating…" : "Create on YouTube"}
          </Button>
        )}
        {!isActive && !needsCreation && (
          <Button onClick={() => reconcile.mutate(kind)} disabled={reconcile.isPending}>
            {reconcile.isPending ? "Verifying…" : "Verify / adopt"}
          </Button>
        )}
        {confirmingDelete ? (
          <Button
            variant="danger"
            onClick={() => {
              remove.mutate(kind);
              setConfirmingDelete(false);
            }}
          >
            {playlist.playlistId
              ? `Delete ${playlist.playlistId} from YouTube — confirm`
              : "Remove the local leftover — confirm"}
          </Button>
        ) : (
          <Button variant="danger" onClick={() => setConfirmingDelete(true)}>
            {isActive ? "Delete playlist" : "Delete setup artifact"}
          </Button>
        )}
      </div>
      {remove.isError && <p className={styles.warn}>{publishError(remove.error)}</p>}
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
            data && data.tunerPlaylists.length < 3 ? (
              <Button
                variant="primary"
                onClick={() => setup.mutate()}
                disabled={setup.isPending}
              >
                {setup.isPending
                  ? "Creating…"
                  : data.tunerPlaylists.length === 0
                    ? "Create the three playlists"
                    : "Create missing playlists"}
              </Button>
            ) : undefined
          }
        >
          {isLoading && <p className={styles.meta}>Loading…</p>}
          {setup.isPending && (
            <Busy label="Creating the playlists on YouTube and verifying — up to a minute…" />
          )}
          {data && data.tunerPlaylists.length === 0 && !setup.isPending && (
            <EmptyState message="Not created yet. Preview first, then create them in one confirmed step." />
          )}
          {setup.isError && <p className={styles.warn}>{publishError(setup.error)}</p>}
          {setup.data && (
            <ul className={styles.results}>
              {setup.data.playlists.map((result) => (
                <li key={result.kind}>
                  <strong>{result.kind}</strong> — {setupOutcome(result)}
                </li>
              ))}
            </ul>
          )}
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
                  <p className={styles.meta}>{describeTrackCount(playlist.trackCount)}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}
