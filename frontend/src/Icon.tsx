// Line icons of the visual refresh (#186): 24 px grid, stroke 1.7, round caps. Inline SVG, no icon library.
// ponytail: one path list per icon; add a name here when a screen needs a new one.
const PATHS = {
  terminal: ['M4 5h16v14H4z', 'm8 10 2 2-2 2', 'M13 14h3'],
  activity: ['M3 12h4l2-6 4 12 2-6h6'],
  box: ['m12 3 8 4.5v9L12 21l-8-4.5v-9z', 'm4 7.5 8 4.5 8-4.5', 'M12 12v9'],
  grid: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
  layers: ['m12 4 9 5-9 5-9-5z', 'm3 14 9 5 9-5'],
  user: ['M8 8a4 4 0 1 0 8 0 4 4 0 1 0-8 0', 'M4 20a8 8 0 0 1 16 0'],
  users: ['M5 8a3.5 3.5 0 1 0 7 0 3.5 3.5 0 1 0-7 0', 'M2 20a6.5 6.5 0 0 1 13 0', 'M16 4.5a3.5 3.5 0 0 1 0 7', 'M18 14a6 6 0 0 1 4 6'],
  wrench: ['M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3l7.5-7.5a4 4 0 0 0-2-2z'],
  shield: ['M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z', 'm9 12 2 2 4-4'],
  'shield-x': ['M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z', 'm10 10 4 4', 'm14 10-4 4'],
  list: ['M9 6h11', 'M9 12h11', 'M9 18h11', 'M4 6h.01', 'M4 12h.01', 'M4 18h.01'],
  alert: ['M12 4 2.5 20h19z', 'M12 10v4', 'M12 17h.01'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  lock: ['M5 11h14v10H5z', 'M8 11V8a4 4 0 0 1 8 0v3'],
  chip: ['M7 7h10v10H7z', 'M10 10h4v4h-4z', 'M10 3v4', 'M14 3v4', 'M10 17v4', 'M14 17v4', 'M3 10h4', 'M3 14h4', 'M17 10h4', 'M17 14h4'],
  globe: ['M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0', 'M3 12h18', 'M12 3c2.5 2.5 3.5 5.5 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-5.5-3.5-9S9.5 5.5 12 3'],
  'folder-x': ['M3 6h6l2 2h10v11H3z', 'm10 11 4 4', 'm14 11-4 4'],
  refresh: ['M20 11a8 8 0 0 0-14.5-4.5L4 8', 'M4 4v4h4', 'M4 13a8 8 0 0 0 14.5 4.5L20 16', 'M20 20v-4h-4'],
  plus: ['M12 5v14', 'M5 12h14'],
  'trend-up': ['M7 17L17 7M9 7h8v8'],
  'trend-down': ['M7 7l10 10M17 9v8H9'],
  // Added for claims: the document and the route nodes of the graph strip.
  file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4', 'M9 13h6', 'M9 17h6'],
  route: ['M6 4v10a4 4 0 0 0 4 4h8', 'm15 15 3 3-3 3', 'M6 4h.01'],
} as const

export type IconName = keyof typeof PATHS
export const ICON_NAMES = Object.keys(PATHS) as IconName[]

// Decorative: the text next to the icon gives the name. An icon-only button needs its own aria-label.
// Color comes from the parent (currentColor).
export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  )
}
