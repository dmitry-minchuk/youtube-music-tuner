import type { ReactNode } from "react";
import styles from "@/ui/Panel.module.css";

export function Panel({
  title,
  description,
  actions,
  children,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}): React.JSX.Element {
  return (
    <section className={styles.panel}>
      {(title || actions) && (
        <header className={styles.header}>
          <div>
            {title && <h2>{title}</h2>}
            {description && <p className={styles.description}>{description}</p>}
          </div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeading({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}): React.JSX.Element {
  return (
    <div className={styles.heading}>
      <h1>{title}</h1>
      {subtitle && <p className={styles.description}>{subtitle}</p>}
    </div>
  );
}

export function EmptyState({
  message,
  action,
}: {
  message: string;
  action?: ReactNode;
}): React.JSX.Element {
  return (
    <div className={styles.empty}>
      <p>{message}</p>
      {action}
    </div>
  );
}
