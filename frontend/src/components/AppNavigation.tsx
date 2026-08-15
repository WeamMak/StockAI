import { Icon } from "./Icon";

interface AppNavigationProps {
  active: "home" | "scans";
  onHome: () => void;
  onScans: () => void;
}

export function AppNavigation({ active, onHome, onScans }: AppNavigationProps) {
  return (
    <nav aria-label="Application navigation" className="app-navigation">
      <button
        aria-current={active === "home" ? "page" : undefined}
        className="navigation-item"
        type="button"
        onClick={onHome}
      >
        <Icon name="home" />
        <span>Home</span>
      </button>
      <button
        aria-current={active === "scans" ? "page" : undefined}
        className="navigation-item"
        type="button"
        onClick={onScans}
      >
        <Icon name="scans" />
        <span>Scans</span>
      </button>
    </nav>
  );
}
