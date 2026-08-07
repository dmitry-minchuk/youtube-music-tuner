import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export type Mood = "ANY" | "FOCUS" | "ENERGY" | "CALM" | "BACKGROUND" | "REDISCOVER";

export const MOODS: { id: Mood; label: string }[] = [
  { id: "ANY", label: "Any" },
  { id: "FOCUS", label: "Focus" },
  { id: "ENERGY", label: "Energy" },
  { id: "CALM", label: "Calm" },
  { id: "BACKGROUND", label: "Background" },
  { id: "REDISCOVER", label: "Rediscover" },
];

export interface WaveItemDto {
  position: number;
  track: { videoId: string; title: string; artists: string[] };
  reasonCodes: string[];
  familiarity: "FAMILIAR" | "DISCOVERY";
}

export interface WaveResponse {
  queueId: string;
  generationId: string;
  ranking: {
    phase: "BASELINE" | "SHADOW" | "ACTIVE";
    servingPolicy: string;
    servingModelId: string | null;
    shadowModelId: string | null;
    qualityScoreSource: string;
  };
  mix: {
    targetFamiliarPercent: number;
    actualFamiliarPercent: number;
    actualDiscoveryPercent: number;
  };
  relaxations: string[];
  items: WaveItemDto[];
}

export function useCreateWave() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { temperature: number; mood: Mood; length?: number }) =>
      api.post<WaveResponse>("/api/v1/waves", {
        temperature: input.temperature,
        mood: input.mood,
        length: input.length ?? 40,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["system"] });
      // The learning label can change the moment a wave is generated under a
      // new phase (e.g. the first ACTIVE-served wave).
      void client.invalidateQueries({ queryKey: ["insights", "learning"] });
    },
  });
}

export function useExtendWave() {
  return useMutation({
    mutationFn: (queueId: string) =>
      api.post<WaveResponse>(`/api/v1/waves/${encodeURIComponent(queueId)}/extend`),
  });
}

export function usePatchWave() {
  return useMutation({
    mutationFn: (input: { queueId: string; temperature: number; mood: Mood }) =>
      api.patch<WaveResponse>(`/api/v1/waves/${encodeURIComponent(input.queueId)}`, {
        temperature: input.temperature,
        mood: input.mood,
      }),
  });
}

/** The slider shows the expected mix, not an abstract number (docs/06 s.4). */
export function describeMix(familiarPercent: number): string {
  return `${familiarPercent}% familiar · ${100 - familiarPercent}% discovery`;
}

export function expectedFamiliarPercent(temperature: number): number {
  if (temperature <= 25) return 80;
  if (temperature <= 60) return 55;
  if (temperature <= 85) return 30;
  return 15;
}
