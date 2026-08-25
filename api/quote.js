const ALLOWED_SYMBOLS = new Set(['AXTI']);

function latestQuote(result) {
  const meta = result?.meta;
  if (!meta) return null;

  const timestamps = result.timestamp ?? [];
  const closes = result.indicators?.quote?.[0]?.close ?? [];
  const candidates = [
    { price: meta.regularMarketPrice, time: meta.regularMarketTime, session: 'regular' },
    { price: meta.postMarketPrice, time: meta.postMarketTime, session: 'after-hours' },
    { price: meta.preMarketPrice, time: meta.preMarketTime, session: 'pre-market' },
  ];

  for (let index = Math.min(timestamps.length, closes.length) - 1; index >= 0; index -= 1) {
    if (Number.isFinite(closes[index]) && closes[index] > 0) {
      candidates.push({
        price: closes[index],
        time: timestamps[index],
        session: 'latest chart',
      });
      break;
    }
  }

  const latest = candidates
    .filter(({ price, time }) => Number.isFinite(price) && price > 0 && Number.isFinite(time))
    .sort((left, right) => right.time - left.time)[0];

  if (!latest) return null;
  return {
    price: latest.price,
    previousClose: meta.previousClose ?? meta.chartPreviousClose ?? latest.price,
    updatedAt: latest.time,
    session: latest.session,
  };
}

module.exports = async function handler(request, response) {
  if (request.method !== 'GET') {
    response.setHeader('Allow', 'GET');
    return response.status(405).json({ error: 'Method not allowed' });
  }

  const rawSymbol = Array.isArray(request.query.symbol)
    ? request.query.symbol[0]
    : request.query.symbol;
  const symbol = String(rawSymbol || 'AXTI').toUpperCase();
  if (!ALLOWED_SYMBOLS.has(symbol)) {
    return response.status(400).json({ error: 'Unsupported symbol' });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6_000);

  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1m&range=5d&includePrePost=true`;
    const upstream = await fetch(url, {
      headers: { 'User-Agent': 'mini-hft-portfolio/1.0' },
      signal: controller.signal,
    });
    if (!upstream.ok) throw new Error(`Upstream returned ${upstream.status}`);

    const quote = latestQuote((await upstream.json())?.chart?.result?.[0]);
    if (!quote) throw new Error('Upstream response did not contain a quote');

    response.setHeader('Cache-Control', 's-maxage=15, stale-while-revalidate=60');
    return response.status(200).json(quote);
  } catch {
    return response.status(502).json({ error: 'Quote temporarily unavailable' });
  } finally {
    clearTimeout(timeout);
  }
};
