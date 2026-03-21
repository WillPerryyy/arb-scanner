import { useState } from "react";
import type { EvEdgeOpportunity } from "../../types/arbitrage";
import { EvEdgeCard } from "./EvEdgeCard";

interface Props {
  edges: EvEdgeOpportunity[];
}

function EvEdgeExplainer() {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-amber-800/40 bg-amber-950/20 overflow-hidden">
      {/* Toggle header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left
                   hover:bg-amber-900/10 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-amber-400 text-sm font-semibold">What is an EV+ Edge?</span>
          <span className="px-1.5 py-0.5 rounded text-xs bg-amber-900/40 text-amber-300 border border-amber-800/40">
            EV+
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-amber-600 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {/* Explanation body */}
      {open && (
        <div className="px-4 pb-4 space-y-3 text-xs text-gray-300 border-t border-amber-800/30">
          <div className="pt-3 space-y-2">
            <p>
              An <span className="text-amber-300 font-semibold">EV+ Edge</span> is a two-leg bet where you place money
              on <span className="text-white">both sides</span> of the same event — one leg on a sportsbook
              (DraftKings, FanDuel, etc.), the other on a prediction market (Kalshi).
            </p>
            <p>
              Unlike a <span className="text-green-400">Guaranteed Arb</span>, you will not profit on every outcome.
              One leg wins, one leg loses. However, the sportsbook and prediction market have
              <span className="text-white"> inconsistent pricing</span> — meaning the combined stakes buy you
              better-than-fair <span className="text-amber-300">expected value</span> over many bets.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1">
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-amber-400 font-semibold mb-1">Pinnacle-weighted EV</p>
              <p className="text-gray-400">
                The primary signal. Uses the Pinnacle Consensus line as a "true probability" oracle
                to weight each outcome. Positive means the bet has mathematical edge over time.
              </p>
            </div>
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-white font-semibold mb-1">Max loss</p>
              <p className="text-gray-400">
                The worst-case loss as a % of your total outlay. Stakes are sized so max loss ≤ 10%.
                You can rescale stakes to any dollar amount by clicking a card.
              </p>
            </div>
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-red-400 font-semibold mb-1">Risk profile</p>
              <p className="text-gray-400">
                This is a <span className="text-white">probabilistic bet</span>, not a lock.
                One leg always loses. Only place EV+ edges you can afford to repeat many times —
                the edge materialises in aggregate, not per trade.
              </p>
            </div>
          </div>

          <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800 space-y-1">
            <p className="text-gray-400 font-semibold text-xs">Example</p>
            <p className="text-gray-400">
              DraftKings offers <span className="text-white">+220</span> on Team A to win (implied 31.3%).
              Kalshi prices Team A at <span className="text-white">¢38</span> (38% implied).
              Pinnacle Consensus has Team A at <span className="text-amber-300">36%</span>.
              The sportsbook is under-pricing a 36% outcome at 31.3% — buying that side
              alongside a Kalshi hedge produces a positive Pinnacle-weighted EV.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function EvEdgeList({ edges }: Props) {
  if (edges.length === 0) {
    return (
      <div className="space-y-4">
        <EvEdgeExplainer />
        <div className="text-center py-12 text-gray-500">
          <p className="text-lg font-medium">No EV+ edge opportunities found</p>
          <p className="text-sm mt-1 max-w-sm mx-auto text-gray-600">
            Edges appear when sportsbook and prediction market pricing diverges enough
            that one side offers better-than-fair expected return.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <EvEdgeExplainer />
      <p className="text-xs text-gray-500 pt-1">
        {edges.length} opportunit{edges.length === 1 ? "y" : "ies"} · sorted by avg return % · click to expand
      </p>
      {edges.map(edge => (
        <EvEdgeCard key={edge.id} edge={edge} />
      ))}
    </div>
  );
}
