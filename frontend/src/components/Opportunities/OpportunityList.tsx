import type { ArbitrageOpportunity } from "../../types/arbitrage";
import { OpportunityCard } from "./OpportunityCard";

interface Props {
  opportunities: ArbitrageOpportunity[];
}

export function OpportunityList({ opportunities }: Props) {
  if (opportunities.length === 0) {
    return (
      <div className="text-center py-16 text-gray-500">
        <p className="text-lg font-medium">No arbitrage opportunities found</p>
        <p className="text-sm mt-1">
          Markets are scanning every 45 seconds. Opportunities are rare — try
          lowering the min profit filter.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500">
        {opportunities.length} opportunit{opportunities.length === 1 ? "y" : "ies"} — click to expand
      </p>
      {opportunities.map(opp => (
        <OpportunityCard key={opp.id} opp={opp} />
      ))}
    </div>
  );
}
