export type IconName =
  | "wave"
  | "collection"
  | "playlists"
  | "insights"
  | "settings"
  | "previous"
  | "next"
  | "play"
  | "pause"
  | "heart"
  | "dislike"
  | "ban"
  | "volume";

const PATHS: Record<IconName, React.JSX.Element> = {
  wave: <path d="M3 12h2.5l2-6 3.5 12 2.5-8 2 5H21" />,
  collection: <path d="M20.8 4.7a5.5 5.5 0 0 0-7.8 0L12 5.8l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.4 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z" />,
  playlists: (
    <>
      <path d="M4 6h10M4 11h10M4 16h7" />
      <path d="m16 14 5 3-5 3Z" />
    </>
  ),
  insights: (
    <>
      <path d="M5 20V10M12 20V4M19 20v-7" />
      <path d="M3 20h18" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </>
  ),
  previous: (
    <>
      <path d="M6 5v14" />
      <path d="m18 6-9 6 9 6Z" />
    </>
  ),
  next: (
    <>
      <path d="M18 5v14" />
      <path d="m6 6 9 6-9 6Z" />
    </>
  ),
  play: <path d="m8 5 11 7-11 7Z" />,
  pause: (
    <>
      <path d="M9 5v14M15 5v14" />
    </>
  ),
  heart: <path d="M20.8 4.7a5.5 5.5 0 0 0-7.8 0L12 5.8l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.4 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z" />,
  dislike: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 8.5 7 7M15.5 8.5l-7 7" />
    </>
  ),
  // The full-diagonal strike escalates from dislike's small cross: not one
  // track crossed out, the whole direction barred.
  ban: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M5.64 5.64 18.36 18.36" />
    </>
  ),
  volume: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4Z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18 6a8.5 8.5 0 0 1 0 12" />
    </>
  ),
};

export function Icon({
  name,
  size = 20,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}): React.JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {PATHS[name]}
    </svg>
  );
}
