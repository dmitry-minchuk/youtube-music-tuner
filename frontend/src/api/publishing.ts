import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export type Kind = "FAMILIAR" | "BALANCE" | "DISCOVERY";

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
}

export interface PublicationResponse {
  publicationId: string;
  status: "PLANNED" | "WRITING" | "VERIFYING" | "COMPLETE" | "PARTIAL" | "FAILED";
  desiredHash: string;
  effectiveTargetSize: number;
  remainingItemChanges: number;
  remainingEstimatedRequests: number;
  nextContinuationAfter: string | null;
  errorCode: string | null;
  gateVersion: string;
}

export function usePlanPlaylist() {
  return useMutation({
    mutationFn: (kind: Kind) => api.post<PlanResponse>(`/api/v1/managed-playlists/${kind}/plan`),
  });
}

export function useSetupPlaylists() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ playlists: unknown[] }>("/api/v1/managed-playlists/setup", {}),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["playlists"] }),
  });
}

export function usePublishPlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) =>
      api.post<PublicationResponse>(`/api/v1/managed-playlists/${kind}/publish`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["playlists"] }),
  });
}

export function useReconcilePlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) =>
      api.post<{ status: string; playlistId: string | null }>(
        `/api/v1/managed-playlists/${kind}/reconcile`,
      ),
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
