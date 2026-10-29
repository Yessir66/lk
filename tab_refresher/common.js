// Intervals are stored in seconds and shown in the largest unit that divides them: 90 -> 90 s, 120 -> 2 min.
export const UNITS = [
  { seconds: 1, label: 's', badge: 's' },
  { seconds: 60, label: 'min', badge: 'm' },
  { seconds: 3600, label: 'h', badge: 'h' },
];

export function splitInterval(seconds) {
  const unit = [...UNITS].reverse().find((u) => seconds % u.seconds === 0);
  return { value: seconds / unit.seconds, unit };
}

export function formatInterval(seconds) {
  const { value, unit } = splitInterval(seconds);
  return `${value} ${unit.label}`;
}

export function badgeText(seconds) {
  const { value, unit } = splitInterval(seconds);
  return `${value}${unit.badge}`;
}

// Interval the shortcut uses, and the popup proposes, for a tab that is not refreshed yet.
export const DEFAULT_INTERVAL = 60;
