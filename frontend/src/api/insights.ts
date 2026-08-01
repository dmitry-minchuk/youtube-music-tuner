import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface LearningStatus {
  phase: "BASELINE" | "SHADOW" | "ACTIVE";
  label: string;
  servingPolicy: string;
  servingModelId: string | null;
  shadowModelId: string | null;
  qualifiedSessions: number;
  distinctTracks: number;
  positiveSessions: number;
  negativeSessions: number;
  thresholds: {
    sessions: number;
    tracks: number;
    positive: number;
    negative: number;
    cleanBaseline: number;
  };
  bootstrapReady: boolean;
  baselineComplete: boolean;
}

export interface InsightsSummary {
  period: string;
  sampleSize: number;
  earlySkipRate: number | null;
  completionRate: number | null;
  absoluteTimeSessions: number;
  playedFamiliar: number;
  playedDiscovery: number;
  topPositiveArtists: { artist: string; sessions: number; meanReward: number }[];
  topNegativeArtists: { artist: string; sessions: number; meanReward: number }[];
  lastTrainingAt: string | null;
}

export interface ApiBudget {
  librarySync: { used: number; limit: number };
  playlistRequests: { used: number; limit: number };
  externalCallsLast24h: number;
  circuit: { open: boolean; reason: string | null; retryAfter: string | null };
}

export function useLearningStatus() {
  return useQuery({
    queryKey: ["insights", "learning"],
    queryFn: ({ signal }) => api.get<LearningStatus>("/api/v1/insights/learning-status", signal),
  });
}

export function useInsightsSummary(period: "7d" | "30d" | "90d" = "30d") {
  return useQuery({
    queryKey: ["insights", "summary", period],
    queryFn: ({ signal }) =>
      api.get<InsightsSummary>(`/api/v1/insights/summary?period=${period}`, signal),
  });
}

export function useApiBudget() {
  return useQuery({
    queryKey: ["insights", "budget"],
    queryFn: ({ signal }) => api.get<ApiBudget>("/api/v1/diagnostics/api-budget", signal),
  });
}

export function formatPercent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}
