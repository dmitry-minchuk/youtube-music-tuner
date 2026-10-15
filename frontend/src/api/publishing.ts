import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export type Kind = "FAMILIAR" | "BALANCE" | "DISCOVERY";

export interface PlanTrack {
  videoId: string;
  title: string;
  artists: string[];
  familiarity: "FAMILIAR" | "DISCOVERY";
  reasonCodes: string[];
}

export interface PlanResponse {
  status: "READY" | "SKIPPED_QUALITY";
  reasonCode?: string;
  configuredTargetSize: number;
  effectiveTargetSize: number;
  minimumPublishSize: number;
  requiredFamiliar: number;
  availableFamiliar: number;
  requiredDiscovery: number;
  availableDiscovery: number;
  gateVersion: string;
  gateFailures: string[];
  gateSkipped: string[];
  reasonCodes: string[];
  videoIds: string[];
  tracks: PlanTrack[];
  randomSeed?: number | null;
  generatedAt?: string | null;
}

export function usePlanPlaylist() {
  return useMutation({
    mutationFn: ({ kind, regenerate = false }: { kind: Kind; regenerate?: boolean }) =>
      api.post<PlanResponse>(`/api/v1/managed-playlists/${kind}/plan`, { regenerate }),
  });
}

export interface SetupResult {
  kind: Kind;
  status: string;
  reasonCode?: string;
  errorCode?: string | null;
  alreadyExisting?: boolean;
  effectiveTargetSize?: number;
  minimumPublishSize?: number;
  availableFamiliar?: number;
  availableDiscovery?: number;
  gateFailures?: string[];
}

export function useSetupPlaylists() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<{ playlists: SetupResult[] }>("/api/v1/managed-playlists/setup", {}),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["playlists"] }),
  });
}

export interface RegenerateResponse {
  kind: Kind;
  status: "COMPLETE" | "SKIPPED_QUALITY" | "FAILED";
  errorCode: string | null;
  reasonCode?: string;
  ratingsApplied: number;
  added: number;
  removed: number;
  removedDisliked: number;
  removedAfterWrite: number;
  publicationId: string | null;
  tracks: PlanTrack[];
  effectiveTargetSize?: number;
  availableFamiliar?: number;
  availableDiscovery?: number;
  minimumPublishSize?: number;
  gateFailures?: string[];
}

export function useRegeneratePlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) =>
      api.post<RegenerateResponse>(`/api/v1/managed-playlists/${kind}/regenerate`),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["playlists"] });
      void client.invalidateQueries({ queryKey: ["rating"] });
      void client.invalidateQueries({ queryKey: ["library"] });
    },
  });
}

export interface ReconcileResponse {
  kind: Kind;
  status: string;
  playlistId: string | null;
  errorCode: string | null;
}

export function useReconcilePlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) =>
      api.post<ReconcileResponse>(`/api/v1/managed-playlists/${kind}/reconcile`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["playlists"] }),
  });
}

export function useDeleteSetupArtifact() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) =>
      api.delete<{ status: string }>(`/api/v1/managed-playlists/${kind}/setup-artifact`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["playlists"] }),
  });
}

const GATE_TEXT: Record<string, string> = {
  INSUFFICIENT_POOL: "Not enough eligible tracks yet",
  QUOTA_OUT_OF_TOLERANCE: "Familiar/discovery mix is too far from the target",
  NOT_ENOUGH_ARTISTS: "Too few distinct artists",
  ARTIST_OVER_REPRESENTED: "One artist appears too often",
  ADJACENT_SAME_ARTIST: "Two tracks by the same artist sit next to each other",
  NEGATIVE_QUALITY_EXPECTED: "A candidate scores below the quality floor",
  WORSE_THAN_CURRENT_PLAYLIST: "The new list scores worse than the current one",
  EXPIRED_DISCOVERY_EDGE: "Some discovery picks are based on stale data",
  SEED_OVER_REPRESENTED: "One seed dominates the list",
  DUPLICATE_TRACKS: "The list contains duplicates",
  INELIGIBLE_TRACK: "The list contains a blocked or unplayable track",
  SIZE_OUT_OF_RANGE: "The list is shorter than the minimum publishable size",
};

export function describeGate(code: string): string {
  return GATE_TEXT[code] ?? code.toLowerCase().replaceAll("_", " ");
}
