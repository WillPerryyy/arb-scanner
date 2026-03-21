import type { ArbitrageOpportunity, ArbLeg } from "../types/arbitrage";

/**
 * Proportionally rescale both legs of an arb opportunity to a desired total outlay.
 *
 * All monetary values (stakes, costs, payouts, net profit) scale linearly.
 * Dimensionless ratios (ROI%, expected value, match score) are unchanged — the
 * arb's edge per dollar is fixed regardless of position size.
 *
 * @param opp          - The base opportunity (as received from the API).
 * @param targetOutlay - Desired total dollars to invest across both legs.
 * @returns A new ArbitrageOpportunity with all monetary fields scaled.
 */
export function rescaleOpportunity(
  opp: ArbitrageOpportunity,
  targetOutlay: number,
): ArbitrageOpportunity {
  if (opp.total_cost <= 0 || targetOutlay <= 0) return opp;

  const scale = targetOutlay / opp.total_cost;

  function scaleLeg(leg: ArbLeg): ArbLeg {
    return {
      ...leg,
      stake:           leg.stake           * scale,
      effective_cost:  leg.effective_cost  * scale,
      expected_payout: leg.expected_payout * scale,
    };
  }

  return {
    ...opp,
    leg_yes:           scaleLeg(opp.leg_yes),
    leg_no:            scaleLeg(opp.leg_no),
    total_cost:        targetOutlay,
    guaranteed_return: opp.guaranteed_return * scale,
    net_profit:        opp.net_profit        * scale,
    // net_profit_pct and expected_value are ratios — unchanged by scaling
  };
}
