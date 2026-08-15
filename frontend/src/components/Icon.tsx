export type IconName =
  | "alert"
  | "check"
  | "coverage"
  | "document"
  | "home"
  | "offer"
  | "preferences"
  | "recommendation"
  | "scans"
  | "shortage";

const paths: Record<IconName, React.ReactNode> = {
  alert: <path d="M12 4 3.5 19h17L12 4Zm0 5v4m0 3h.01" />,
  check: <path d="m7 12 3 3 7-7" />,
  coverage: <path d="M12 3 5 6v5c0 4.6 2.8 7.8 7 10 4.2-2.2 7-5.4 7-10V6l-7-3Zm-3 9 2 2 4-4" />,
  document: <path d="M7 3h7l4 4v14H7V3Zm7 0v5h5M10 12h5m-5 4h5" />,
  home: <path d="m3 11 9-8 9 8m-16 0v10h14V11M9 21v-6h6v6" />,
  offer: <path d="M4 7h16v12H4V7Zm3 0V5h10v2M4 11h16m-10 0v2h4v-2" />,
  preferences: <path d="M4 7h10m4 0h2M4 17h2m4 0h10M14 4v6M7 14v6" />,
  recommendation: <path d="M12 3a7 7 0 0 0-4 12.7V19h8v-3.3A7 7 0 0 0 12 3Zm-3 20h6M9 11l2 2 4-4" />,
  scans: <path d="M4 5h16v14H4V5Zm4 4h8m-8 4h5" />,
  shortage: <path d="M12 3v13m0 5v.01M7 8l5-5 5 5" />,
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      aria-hidden="true"
      className="icon"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
    >
      {paths[name]}
    </svg>
  );
}
