export const ROUTES = ["wave", "collection", "playlists", "insights", "settings"] as const;

export type RouteId = (typeof ROUTES)[number];

export const ROUTE_LABELS: Record<RouteId, string> = {
  wave: "Wave",
  collection: "Collection",
  playlists: "Playlists",
  insights: "Insights",
  settings: "Settings",
};

export function routeFromHash(hash: string): RouteId {
  const candidate = hash.replace(/^#\/?/, "").split("/")[0] as RouteId;
  return ROUTES.includes(candidate) ? candidate : "wave";
}
