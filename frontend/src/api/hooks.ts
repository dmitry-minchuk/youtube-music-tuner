import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface SystemStatus {
  appVersion: string;
  serverTime: string;
  publicPort: number;
  autoPublish: boolean;
  youtube: { connected: boolean; reason: string };
  lastSuccessfulSyncAt: string | null;
  lastTrainingAt: string | null;
  pendingJobs: number;
  externalCallsLast24h: number;
  qualifiedSessions: number;
}

export interface AuthStatus {
  connected: boolean;
  reason: string;
  clientConfigured: boolean;
  tokenPresent: boolean;
}

export function useSystemStatus() {
  return useQuery({
    queryKey: ["system", "status"],
    queryFn: ({ signal }) => api.get<SystemStatus>("/api/v1/system/status", signal),
    refetchInterval: 30_000,
  });
}

export function useAuthStatus() {
  return useQuery({
    queryKey: ["auth", "status"],
    queryFn: ({ signal }) => api.get<AuthStatus>("/api/v1/auth/status", signal),
  });
}
