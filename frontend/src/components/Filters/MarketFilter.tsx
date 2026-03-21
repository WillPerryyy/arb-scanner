import type { FilterState, Platform, ArbType, SortKey } from "../../types/arbitrage";

const PLATFORMS: { value: Platform; label: string }[] = [
  { value: "kalshi",     label: "Kalshi" },
  { value: "polymarket", label: "Polymarket" },
  { value: "predictit",  label: "PredictIt" },
  { value: "draftkings", label: "DraftKings" },
  { value: "fanduel",    label: "FanDuel" },
  { value: "betmgm",     label: "BetMGM" },
  { value: "caesars",    label: "Caesars" },
];

const ARB_TYPES: { value: ArbType; label: string }[] = [
  { value: "cross_platform", label: "Cross Platform" },
  { value: "sportsbook",     label: "Sportsbook" },
  { value: "spread",         label: "Spread Arb" },
];

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "net_profit",     label: "Net Profit $" },
  { value: "net_profit_pct", label: "ROI %" },
  { value: "expected_value", label: "Expected Value" },
  { value: "detected_at",    label: "Newest" },
];

interface Props {
  filters:   FilterState;
  onChange:  (f: FilterState) => void;
}

function toggleItem<T>(arr: T[], item: T): T[] {
  return arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item];
}

export function MarketFilter({ filters, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-4 items-end pb-2 border-b border-gray-800">
      {/* Min profit */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">Min Profit ($)</span>
        <input
          type="number"
          min={0}
          step={0.001}
          value={filters.minProfit}
          onChange={e => onChange({ ...filters, minProfit: parseFloat(e.target.value) || 0 })}
          className="bg-gray-800 border border-gray-700 text-white text-sm rounded px-2 py-1
                     w-24 focus:outline-none focus:border-blue-500"
        />
      </label>

      {/* Sort by */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">Sort by</span>
        <select
          value={filters.sortBy}
          onChange={e => onChange({ ...filters, sortBy: e.target.value as SortKey })}
          className="bg-gray-800 border border-gray-700 text-white text-sm rounded px-2 py-1
                     focus:outline-none focus:border-blue-500"
        >
          {SORT_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      {/* Platform filter */}
      <div className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">Platforms</span>
        <div className="flex gap-1.5 flex-wrap">
          {PLATFORMS.map(p => (
            <button
              key={p.value}
              onClick={() =>
                onChange({ ...filters, platforms: toggleItem(filters.platforms, p.value) })
              }
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors
                ${filters.platforms.includes(p.value)
                  ? "bg-blue-700 text-white border border-blue-500"
                  : "bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-500"
                }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Arb type filter */}
      <div className="flex flex-col gap-1">
        <span className="text-xs text-gray-500">Type</span>
        <div className="flex gap-1.5">
          {ARB_TYPES.map(t => (
            <button
              key={t.value}
              onClick={() =>
                onChange({ ...filters, arbTypes: toggleItem(filters.arbTypes, t.value) })
              }
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors
                ${filters.arbTypes.includes(t.value)
                  ? "bg-purple-700 text-white border border-purple-500"
                  : "bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-500"
                }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
