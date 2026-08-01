import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Mood } from "@/api/wave";

export interface AppSettings {
  pauseOnHidden: boolean;
  defaultTemperature: number;
  defaultMood: Mood;
  volume: number;
}

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => api.get<AppSettings>("/api/v1/settings", signal),
    staleTime: 60_000,
  });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<AppSettings>) => api.patch<AppSettings>("/api/v1/settings", patch),
    // Toggles must flip instantly; the response confirms.
    onMutate: async (patch) => {
      await client.cancelQueries({ queryKey: ["settings"] });
      const previous = client.getQueryData<AppSettings>(["settings"]);
      if (previous) client.setQueryData<AppSettings>(["settings"], { ...previous, ...patch });
      return { previous };
    },
    onError: (_error, _patch, context) => {
      if (context?.previous) client.setQueryData(["settings"], context.previous);
    },
    onSuccess: (data) => client.setQueryData(["settings"], data),
  });
}
