import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "@/api/client";
import type { AuthStatus } from "@/api/hooks";
import { Button } from "@/ui/Button";
import styles from "@/features/settings/ConnectPanel.module.css";

interface ConnectResult {
  connected: boolean;
  method: string;
  likedTracksVisible: number;
}

function connectError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "VALIDATION_FAILED") return error.message;
    if (error.code === "YTM_AUTH_REQUIRED") {
      return "YouTube Music rejected these headers. Make sure you copied them from a tab where you are signed in.";
    }
    if (error.code === "YTM_UNAVAILABLE") return "YouTube Music did not answer. Try again in a moment.";
    return error.message;
  }
  return "Could not reach the local API.";
}

/**
 * Connecting used to require pasting headers into a terminal. The paste now
 * happens here instead — same value, same 0600 file, but no shell. The text
 * is sent once and never rendered back (docs/03 section 2).
 */
export function ConnectPanel({ auth }: { auth: AuthStatus | undefined }): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [paste, setPaste] = useState("");
  const client = useQueryClient();

  const connect = useMutation({
    mutationFn: (headers: string) =>
      api.post<ConnectResult>("/api/v1/auth/browser-headers", { headers }),
    onSuccess: () => {
      setPaste("");
      setOpen(false);
      void client.invalidateQueries({ queryKey: ["auth"] });
      void client.invalidateQueries({ queryKey: ["system"] });
    },
  });

  const connected = auth?.connected ?? false;

  return (
    <div className={styles.panel}>
      <div className={styles.row}>
        <div>
          <p className={styles.state}>
            {connected ? "Connected to YouTube Music" : "Not connected"}
          </p>
          <p className={styles.hint}>
            {connected
              ? "Reconnect if syncing starts failing — browser sessions expire eventually."
              : "Tuner needs a signed-in browser session to read your library."}
          </p>
        </div>
        <Button variant={connected ? "secondary" : "primary"} onClick={() => setOpen(!open)}>
          {open ? "Cancel" : connected ? "Reconnect" : "Connect"}
        </Button>
      </div>

      {open && (
        <div className={styles.form}>
          <ol className={styles.steps}>
            <li>
              Open{" "}
              <a href="https://music.youtube.com" target="_blank" rel="noreferrer">
                music.youtube.com
              </a>{" "}
              and make sure you are signed in.
            </li>
            <li>
              Press <kbd>F12</kbd> → <strong>Network</strong>, type <code>youtubei</code> in the
              filter, then click anything in YouTube Music so a request appears.
            </li>
            <li>
              Right-click that request → <strong>Copy</strong> →{" "}
              <strong>Copy request headers</strong>.
            </li>
            <li>Paste below.</li>
          </ol>

          <label className={styles.field}>
            <span className="visually-hidden">Request headers</span>
            <textarea
              value={paste}
              onChange={(event) => setPaste(event.target.value)}
              placeholder={"accept: */*\ncookie: …\nuser-agent: …"}
              rows={7}
              spellCheck={false}
              autoComplete="off"
            />
          </label>

          <div className={styles.actions}>
            <Button
              variant="primary"
              disabled={paste.trim().length === 0 || connect.isPending}
              onClick={() => connect.mutate(paste)}
            >
              {connect.isPending ? "Checking with YouTube Music…" : "Check and connect"}
            </Button>
            <span className={styles.hint}>
              Stored as a 0600 file inside the container, never shown again.
            </span>
          </div>

          {connect.isError && (
            <p className={styles.error} role="status">
              {connectError(connect.error)}
            </p>
          )}
        </div>
      )}

      {connect.isSuccess && connect.data && (
        <p className={styles.success} role="status">
          Connected — {connect.data.likedTracksVisible} liked track
          {connect.data.likedTracksVisible === 1 ? "" : "s"} visible.
        </p>
      )}
    </div>
  );
}
