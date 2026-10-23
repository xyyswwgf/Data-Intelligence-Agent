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
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5",
  search: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M21 21l-4.3-4.3",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  users: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  key: "M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zM15.5 7.5l3 3L22 7l-3-3",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5",
  link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
  tag: "M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8zM7 7h.01",
  folder: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  activity: "M22 12h-4l-3 9L9 3l-3 9H2",
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
