/**
 * 轻量线性图标（24×24，stroke 风格），避免为十几个图标引入整套图标库。
 */
const PATHS = {
  sparkles: "M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z",
  plus: "M12 5v14M5 12h14",
  send: "M5 12h13M13 6l6 6-6 6",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  table: "M3 5h18v14H3zM3 10h18M9 10v9",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 3",
  code: "M8 8l-4 4 4 4M16 8l4 4-4 4M14 4l-4 16",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 12l5 5L20 7",
  alert: "M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  chevron: "M9 6l6 6-6 6",
  menu: "M4 6h16M4 12h16M4 18h16",
  target: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  coins: "M8 8a6 3 0 1 0 12 0a6 3 0 1 0-12 0M8 8v4c0 1.7 2.7 3 6 3s6-1.3 6-3V8M4 12a6 3 0 0 0 6 3M4 12v4c0 1.7 2.7 3 6 3",
  refund: "M3 7h13a5 5 0 0 1 0 10H8M7 3L3 7l4 4",
  map: "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14",
};

export default function Icon({ name, size = 18, className = "", strokeWidth = 1.8 }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] || PATHS.sparkles} />
    </svg>
  );
}
