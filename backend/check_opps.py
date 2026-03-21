import httpx, asyncio
from collections import Counter

async def check():
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get('http://localhost:8000/api/opportunities?limit=200')
        data = r.json()
        opps = data['opportunities']
        print(f'Total: {len(opps)} opportunities')

        types = Counter(o['arb_type'] for o in opps)
        print('By type:', dict(types))

        pairs = Counter(
            o['leg_yes']['contract']['platform'] + ' x ' + o['leg_no']['contract']['platform']
            for o in opps
        )
        print('By platform pair:')
        for k, v in pairs.most_common(15):
            print(f'  {k}: {v}')

        print('\nSample opportunities:')
        for o in opps[:20]:
            yleg = o['leg_yes']
            nleg = o['leg_no']
            title = o['event_title'][:70]
            a_platform = yleg['contract']['platform']
            a_label = yleg['contract']['outcome_label']
            a_price = yleg['contract']['price']
            b_platform = nleg['contract']['platform']
            b_label = nleg['contract']['outcome_label']
            b_price = nleg['contract']['price']
            print(f'  [{o["arb_type"]}] {title}')
            print(f'    A: {a_platform} [{a_label}] @ ${a_price:.3f}')
            print(f'    B: {b_platform} [{b_label}] @ ${b_price:.3f}')
            print(f'    Cost: ${o["total_cost"]:.4f} | Return: ${o["guaranteed_return"]:.4f} | Profit: ${o["net_profit"]:.4f} ({o["net_profit_pct"]:.1f}%)')
            print()

asyncio.run(check())
