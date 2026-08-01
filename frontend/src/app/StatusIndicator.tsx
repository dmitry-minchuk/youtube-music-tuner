import { useSystemStatus } from "@/api/hooks";
import styles from "@/app/StatusIndicator.module.css";

/**
 * Compact indicator, shown only when something needs attention or an
 * operation is running (docs/06 section 2).
 */
export function StatusIndicator(): React.JSX.Element | null {
  const { data, isError } = useSystemStatus();

  if (isError) {
    return (
      <span className={`${styles.chip} ${styles.warn}`} role="status">
        Local API unreachable
      </span>
    );
  }
  if (!data) return null;

  if (!data.youtube.connected) {
    return (
      <a href="#/settings" className={`${styles.chip} ${styles.warn}`}>
        YouTube Music not connected
      </a>
    );
  }
  if (data.pendingJobs > 0) {
    return (
      <span className={styles.chip} role="status">
        {data.pendingJobs} background {data.pendingJobs === 1 ? "task" : "tasks"} running
      </span>
    );
  }
  return null;
}
