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

/** arb types that are both displayed as "Guaranteed Arb" */
const GUARANTEED_TYPES: ArbType[] = ["cross_platform", "spread"];

const ARB_TYPES: { value: ArbType | "guaranteed_arb"; label: string }[] = [
  { value: "guaranteed_arb", label: "Guaranteed Arb" },
  { value: "sportsbook",     label: "Sportsbook" },
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
          {ARB_TYPES.map(t => {
            const isGuaranteed = t.value === "guaranteed_arb";
            const active = isGuaranteed
              ? GUARANTEED_TYPES.some(g => filters.arbTypes.includes(g))
              : filters.arbTypes.includes(t.value as ArbType);
            function handleClick() {
              if (isGuaranteed) {
                // Toggle both cross_platform and spread together
                const without = filters.arbTypes.filter(x => !GUARANTEED_TYPES.includes(x));
                onChange({ ...filters, arbTypes: active ? without : [...without, ...GUARANTEED_TYPES] });
              } else {
                onChange({ ...filters, arbTypes: toggleItem(filters.arbTypes, t.value as ArbType) });
              }
            }
            return (
              <button
                key={t.value}
                onClick={handleClick}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors
                  ${active
                    ? "bg-purple-700 text-white border border-purple-500"
                    : "bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-500"
                  }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
