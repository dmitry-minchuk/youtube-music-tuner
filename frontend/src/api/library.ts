import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface TrackDto {
  videoId: string;
  title: string;
  artists: string[];
  albumTitle: string | null;
  durationSeconds: number | null;
  thumbnailUrl: string | null;
  isPlayable: boolean;
}

export interface LibraryPage {
  items: TrackDto[];
  total: number;
  nextCursor: string | null;
}

export type LibraryView = "liked" | "recent" | "discovered" | "blocked" | "all";

export interface TunerPlaylistDto {
  managedPlaylistId: string;
  playlistId: string | null;
  kind: "FAMILIAR" | "BALANCE" | "DISCOVERY";
  status: "CREATING" | "UNVERIFIED" | "ACTIVE" | "CLEANUP_REQUIRED" | "DELETED";
  temperature: number;
  configuredTargetSize: number;
  lastPublishedAt: string | null;
  autoPublishEnabled: boolean;
}

export interface RemotePlaylistDto {
  playlistId: string;
  title: string;
  /** null when YouTube does not report a size, as for its system playlists. */
  trackCount: number | null;
  fetchedAt: string;
}

export interface PlaylistsResponse {
  tunerPlaylists: TunerPlaylistDto[];
  remotePlaylists: RemotePlaylistDto[];
}

export interface SyncStatus {
  lastSuccessfulSyncAt: string | null;
  nextAllowedAt: string | null;
  activeJob: { jobId: string; status: string } | null;
  likedCount: number;
  playlistCount: number;
}

export function useLibraryTracks(view: LibraryView) {
  return useQuery({
    queryKey: ["library", "tracks", view],
    queryFn: ({ signal }) =>
      api.get<LibraryPage>(`/api/v1/library/tracks?view=${view}&limit=100`, signal),
  });
}

export function usePlaylists() {
  return useQuery({
    queryKey: ["playlists"],
    queryFn: ({ signal }) => api.get<PlaylistsResponse>("/api/v1/playlists", signal),
  });
}

export function useSyncStatus() {
  return useQuery({
    queryKey: ["sync", "status"],
    queryFn: ({ signal }) => api.get<SyncStatus>("/api/v1/sync/status", signal),
  });
}

export function useStartSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ jobId: string; status: string }>("/api/v1/sync"),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["sync"] });
      void client.invalidateQueries({ queryKey: ["system"] });
    },
  });
}

export type RatingState = "LIKE" | "DISLIKE" | "INDIFFERENT";
export type RatingSyncStatus = "SYNCED" | "PENDING" | "FAILED";

export interface TrackRating {
  videoId: string;
  desiredState: RatingState;
  revision: number;
  syncStatus: RatingSyncStatus;
  isLiked: boolean;
  isDisliked: boolean;
}

export function useTrackRating(videoId: string | null) {
  return useQuery({
    queryKey: ["rating", videoId],
    enabled: videoId !== null,
    queryFn: ({ signal }) =>
      api.get<TrackRating>(`/api/v1/tracks/${encodeURIComponent(videoId!)}/rating`, signal),
    // A queued sync settles within a few seconds; poll until it does.
    refetchInterval: (query) =>
      query.state.data?.syncStatus === "PENDING" ? 2000 : false,
  });
}

export function useSetRating() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, desiredState }: { videoId: string; desiredState: RatingState }) =>
      api.put<TrackRating>(`/api/v1/tracks/${encodeURIComponent(videoId)}/rating`, {
        desiredState,
      }),
    // Reflect the click immediately; the poll confirms or corrects it.
    onMutate: async ({ videoId, desiredState }) => {
      await client.cancelQueries({ queryKey: ["rating", videoId] });
      const previous = client.getQueryData<TrackRating>(["rating", videoId]);
      client.setQueryData<TrackRating>(["rating", videoId], (old) => ({
        videoId,
        revision: (old?.revision ?? 0) + 1,
        desiredState,
        syncStatus: "PENDING",
        isLiked: desiredState === "LIKE",
        isDisliked: desiredState === "DISLIKE",
      }));
      return { previous };
    },
    onError: (_error, { videoId }, context) => {
      if (context?.previous) client.setQueryData(["rating", videoId], context.previous);
    },
    onSuccess: (data, { videoId }) => {
      client.setQueryData(["rating", videoId], data);
      void client.invalidateQueries({ queryKey: ["library"] });
    },
  });
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
