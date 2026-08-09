import styles from "@/ui/Busy.module.css";

/**
 * Indeterminate progress bar for operations that genuinely take time
 * (docs/06 s.10): a disabled button alone reads as a hang, a moving bar
 * says "working". Percentages would be a lie — these are single HTTP
 * calls — so the bar is honest about being indeterminate.
 */
export function Busy({ label }: { label: string }): React.JSX.Element {
  return (
    <div className={styles.busy} role="status" aria-busy="true">
      <span className={styles.label}>{label}</span>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.runner} />
      </span>
    </div>
  );
}
