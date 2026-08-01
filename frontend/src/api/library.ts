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
  trackCount: number;
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

export function useSetRating() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, desiredState }: { videoId: string; desiredState: string }) =>
      api.put<{ videoId: string; revision: number; syncStatus: string }>(
        `/api/v1/tracks/${encodeURIComponent(videoId)}/rating`,
        { desiredState },
      ),
    onSuccess: () => {
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
