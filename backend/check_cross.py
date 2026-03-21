import httpx, asyncio

async def check():
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get('http://localhost:8000/api/opportunities?limit=500')
        data = r.json()
        opps = data['opportunities']

        cross = [o for o in opps if o['arb_type'] == 'cross_platform']
        print(f'Cross-platform opportunities: {len(cross)}')

        for o in cross:
            yleg = o['leg_yes']
            nleg = o['leg_no']
            print(f'\n  EVENT: {o["event_title"]}')
            print(f'  Leg A: [{yleg["contract"]["platform"]}] {yleg["contract"]["outcome_label"]}')
            print(f'         price=${yleg["contract"]["price"]:.4f}, market_id={yleg["contract"]["market_id"][:30]}')
            print(f'  Leg B: [{nleg["contract"]["platform"]}] {nleg["contract"]["outcome_label"]}')
            print(f'         price=${nleg["contract"]["price"]:.4f}, market_id={nleg["contract"]["market_id"][:30]}')
            print(f'  Total cost: ${o["total_cost"]:.4f}')
            print(f'  Guaranteed return: ${o["guaranteed_return"]:.4f}')
            print(f'  Net profit: ${o["net_profit"]:.4f} ({o["net_profit_pct"]:.1f}%)')
            print(f'  Match score: {o["match_score"]:.2f}')

asyncio.run(check())
