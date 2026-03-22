type Tab = "arb" | "ev" | "value" | "crypto";

interface Props {
  onNavigate: (tab: Tab) => void;
  counts: {
    arb:    number;
    ev:     number;
    value:  number;
    crypto: number;
  };
}

interface TabCardProps {
  tab:         Tab;
  color:       "green" | "amber" | "blue" | "cyan";
  icon:        string;
  title:       string;
  tagline:     string;
  risk:        "Risk-Free" | "Medium Risk" | "Higher Risk" | "Info Only";
  legs:        string;
  description: string;
  howItWorks:  string;
  whenToUse:   string;
  count:       number;
  onNavigate:  (tab: Tab) => void;
}

const RISK_STYLES: Record<TabCardProps["risk"], string> = {
  "Risk-Free":   "bg-green-900/50 text-green-300 border-green-800/60",
  "Medium Risk": "bg-amber-900/50 text-amber-300 border-amber-800/60",
  "Higher Risk": "bg-red-900/40 text-red-300 border-red-800/60",
  "Info Only":   "bg-cyan-900/40 text-cyan-300 border-cyan-800/60",
};

const COLOR_STYLES = {
  green: {
    border:  "border-green-800/40 hover:border-green-600/60",
    heading: "text-green-400",
    button:  "bg-green-900/40 border-green-700 text-green-300 hover:bg-green-800/50 hover:text-green-200",
    badge:   "bg-green-900/60 text-green-400 border-green-800/60",
    bar:     "bg-green-900/20",
  },
  amber: {
    border:  "border-amber-800/40 hover:border-amber-600/60",
    heading: "text-amber-400",
    button:  "bg-amber-900/40 border-amber-700 text-amber-300 hover:bg-amber-800/50 hover:text-amber-200",
    badge:   "bg-amber-900/60 text-amber-400 border-amber-800/60",
    bar:     "bg-amber-900/20",
  },
  blue: {
    border:  "border-blue-800/40 hover:border-blue-600/60",
    heading: "text-blue-400",
    button:  "bg-blue-900/40 border-blue-700 text-blue-300 hover:bg-blue-800/50 hover:text-blue-200",
    badge:   "bg-blue-900/60 text-blue-400 border-blue-800/60",
    bar:     "bg-blue-900/20",
  },
  cyan: {
    border:  "border-cyan-800/40 hover:border-cyan-600/60",
    heading: "text-cyan-400",
    button:  "bg-cyan-900/40 border-cyan-700 text-cyan-300 hover:bg-cyan-800/50 hover:text-cyan-200",
    badge:   "bg-cyan-900/60 text-cyan-400 border-cyan-800/60",
    bar:     "bg-cyan-900/20",
  },
};

function TabCard({ tab, color, icon, title, tagline, risk, legs, description, howItWorks, whenToUse, count, onNavigate }: TabCardProps) {
  const c = COLOR_STYLES[color];

  return (
    <div className={`bg-gray-900 border rounded-xl overflow-hidden transition-colors ${c.border}`}>
      {/* Card header */}
      <div className={`px-5 py-4 ${c.bar}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="text-2xl">{icon}</span>
            <div>
              <h3 className={`font-bold text-base ${c.heading}`}>{title}</h3>
              <p className="text-gray-400 text-xs mt-0.5">{tagline}</p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${RISK_STYLES[risk]}`}>
              {risk}
            </span>
            <span className="text-xs text-gray-500">{legs}</span>
          </div>
        </div>
      </div>

      {/* Card body */}
      <div className="px-5 py-4 space-y-3 text-xs text-gray-300">
        <p className="text-gray-200 leading-relaxed">{description}</p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
            <p className="text-gray-500 font-semibold uppercase tracking-wide text-[10px] mb-1">How it works</p>
            <p className="text-gray-300 leading-relaxed">{howItWorks}</p>
          </div>
          <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
            <p className="text-gray-500 font-semibold uppercase tracking-wide text-[10px] mb-1">When to use</p>
            <p className="text-gray-300 leading-relaxed">{whenToUse}</p>
          </div>
        </div>

        {/* CTA */}
        <div className="flex items-center justify-between pt-1">
          {count > 0 ? (
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${c.badge}`}>
              {count} live {count === 1 ? "opportunity" : "opportunities"}
            </span>
          ) : (
            <span className="text-gray-600 text-xs">No live opportunities right now</span>
          )}
          <button
            onClick={() => onNavigate(tab)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${c.button}`}
          >
            Open tab
            <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

export function GuidePage({ onNavigate, counts }: Props) {
  return (
    <div className="space-y-6 py-2">
      {/* Intro */}
      <div className="space-y-1">
        <p className="text-gray-300 text-sm leading-relaxed">
          This scanner monitors <span className="text-white font-medium">sportsbooks</span> (DraftKings, FanDuel, Caesars) and{" "}
          <span className="text-white font-medium">prediction markets</span> (Kalshi, Polymarket) in real time,
          looking for pricing discrepancies you can bet into.
          There are four types of opportunity — each with a different risk profile.
        </p>
        <p className="text-gray-500 text-xs">
          Click any card below to open that tab. Opportunities update automatically every ~90 seconds.
        </p>
      </div>

      {/* Tab cards */}
      <div className="space-y-4">
        <TabCard
          tab="arb"
          color="green"
          icon="🔒"
          title="Guaranteed Arb"
          tagline="Lock in profit on both outcomes simultaneously"
          risk="Risk-Free"
          legs="2-leg hedge"
          description="A guaranteed arb exists when the combined implied probabilities across two platforms sum to less than 100% — meaning you can bet both sides of the same event and collect a profit no matter which outcome occurs."
          howItWorks="The scanner finds pairs where a sportsbook and prediction market price the same event differently enough that staking proportionally on each side guarantees a net positive return. Both bets must be placed at the same time before prices move."
          whenToUse="Always — when a guaranteed arb appears, it's a free profit opportunity. The only risks are execution speed (prices change fast) and platform limits on stake size."
          count={counts.arb}
          onNavigate={onNavigate}
        />

        <TabCard
          tab="ev"
          color="amber"
          icon="📊"
          title="EV+ Bets"
          tagline="Two-leg bets with positive expected value over time"
          risk="Medium Risk"
          legs="2-leg hedge"
          description="An EV+ bet places money on both sides of the same event — one leg on a sportsbook, one on Kalshi. Unlike a guaranteed arb, one leg always loses. However, using the Pinnacle Consensus as a true-probability oracle, the probability-weighted return is positive."
          howItWorks="The Pinnacle Consensus line represents the sharpest available market probability. When a sportsbook mis-prices one side relative to that oracle, betting the mispriced side (plus a Kalshi hedge to limit losses) produces positive expected value over many repetitions."
          whenToUse="When you want edge with limited downside. Max loss is capped at ~10% of outlay. Best used at volume — the edge materialises in aggregate. Not a guaranteed profit per trade."
          count={counts.ev}
          onNavigate={onNavigate}
        />

        <TabCard
          tab="value"
          color="blue"
          icon="🎯"
          title="Value Bets"
          tagline="Single bets where the sportsbook underprices an outcome"
          risk="Higher Risk"
          legs="1-leg bet"
          description="A value bet is a single sportsbook bet where the Kalshi prediction market (or Pinnacle Consensus) assigns a meaningfully higher probability to an outcome than the sportsbook's implied odds suggest. No hedge — you win big or lose your stake."
          howItWorks="Kalshi and Pinnacle trade without significant built-in vig, making their prices sharper probability estimates than typical sportsbook lines. When the sportsbook underprices an outcome by ≥ 3 percentage points relative to these oracles, the bet has cross-market positive EV."
          whenToUse="When you're comfortable with binary risk and are betting at volume. Any individual bet can lose your full stake. The edge only shows up in aggregate across many similar bets. Higher frequency than guaranteed arbs but higher per-bet risk."
          count={counts.value}
          onNavigate={onNavigate}
        />

        <TabCard
          tab="crypto"
          color="cyan"
          icon="₿"
          title="Crypto Markets"
          tagline="Kalshi & Polymarket price prediction contracts"
          risk="Info Only"
          legs="Prediction markets"
          description="Displays all live crypto price prediction contracts from Kalshi and Polymarket — covering BTC, ETH, SOL and others across daily, weekly, and longer timeframes. Also flags any cross-platform arbitrage opportunities between the two exchanges."
          howItWorks="Kalshi and Polymarket each offer binary contracts on whether a crypto asset will be above or below a given price at a given time. The scanner compares contracts with matching assets and strike prices across both platforms to surface potential pricing gaps."
          whenToUse="When you want exposure to crypto price moves through prediction markets rather than direct spot/futures, or when you're looking for mispricings between Kalshi and Polymarket on the same contract."
          count={counts.crypto}
          onNavigate={onNavigate}
        />
      </div>

      {/* Footer note */}
      <p className="text-gray-600 text-xs text-center pb-2">
        All signals are for informational purposes only. Verify prices before placing any bet — odds change in real time.
      </p>
    </div>
  );
}
