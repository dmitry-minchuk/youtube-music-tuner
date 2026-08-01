import { ROUTES, ROUTE_LABELS, type RouteId } from "@/app/routes";
import { Icon, type IconName } from "@/ui/Icon";
import styles from "@/app/Sidebar.module.css";

const ROUTE_ICONS: Record<RouteId, IconName> = {
  wave: "wave",
  collection: "collection",
  playlists: "playlists",
  insights: "insights",
  settings: "settings",
};

export function Sidebar({ active }: { active: RouteId }): React.JSX.Element {
  return (
    <nav className={styles.sidebar} aria-label="Sections">
      <div className={styles.brand}>
        <span className={styles.brandMark} aria-hidden="true">
          <Icon name="wave" size={20} />
        </span>
        <span className={styles.brandText}>
          <span className={styles.brandName}>Tuner</span>
          <span className={styles.brandTag}>Personal radio</span>
        </span>
      </div>
      <p className={styles.sectionLabel}>Listen &amp; learn</p>
      <ul className={styles.list}>
        {ROUTES.map((route) => (
          <li key={route}>
            <a
              href={`#/${route}`}
              className={route === active ? `${styles.link} ${styles.linkActive}` : styles.link}
              aria-current={route === active ? "page" : undefined}
            >
              <Icon name={ROUTE_ICONS[route]} className={styles.linkIcon} />
              <span className={styles.linkLabel}>{ROUTE_LABELS[route]}</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
