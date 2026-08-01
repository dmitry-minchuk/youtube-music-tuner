import { ROUTES, ROUTE_LABELS, type RouteId } from "@/app/routes";
import styles from "@/app/Sidebar.module.css";

export function Sidebar({ active }: { active: RouteId }): React.JSX.Element {
  return (
    <nav className={styles.sidebar} aria-label="Sections">
      <div className={styles.brand}>
        <span className={styles.brandMark} aria-hidden="true" />
        <span className={styles.brandName}>Tuner</span>
      </div>
      <ul className={styles.list}>
        {ROUTES.map((route) => (
          <li key={route}>
            <a
              href={`#/${route}`}
              className={route === active ? `${styles.link} ${styles.linkActive}` : styles.link}
              aria-current={route === active ? "page" : undefined}
            >
              {ROUTE_LABELS[route]}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
