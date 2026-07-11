import { SETUP_CHART_DEFAULT_TIMEFRAME } from "./state.js";
import { firstNumber, numberOrNull } from "./ui-helpers.js";

export const SETUP_CHART_TIMEFRAMES = [
  { id: "3m", label: "3mn" },
  { id: "10m", label: "10mn" },
  { id: "15m", label: "15mn" },
  { id: "30m", label: "30mn" },
  { id: "1h", label: "1h" },
  { id: "4h", label: "4h" },
  { id: "1d", label: "1D" },
];

export function normalizeSetupChartTimeframe(value) {
  const normalized = String(value || SETUP_CHART_DEFAULT_TIMEFRAME).trim().toLowerCase();
  const aliases = {
    "3mn": "3m",
    "3min": "3m",
    "10mn": "10m",
    "10min": "10m",
    "15mn": "15m",
    "15min": "15m",
    "30mn": "30m",
    "30min": "30m",
    "60m": "1h",
    "60mn": "1h",
    "1 hour": "1h",
    "4 hours": "4h",
    "1 day": "1d",
    "1D": "1d",
  };
  const id = aliases[normalized] || normalized;
  return SETUP_CHART_TIMEFRAMES.some((item) => item.id === id)
    ? id
    : SETUP_CHART_DEFAULT_TIMEFRAME;
}

export function extractQuoteEvents(events, timeframe = null) {
  const normalizedTimeframe = timeframe ? normalizeSetupChartTimeframe(timeframe) : "";
  const quoteEvents = (events || []).filter((event) => event.event_type === "stock_quote");
  const eventWithBars = quoteEvents.find((event) => {
    const bars = event.data && event.data.historical_bars;
    return Array.isArray(bars)
      && bars.length
      && (!normalizedTimeframe || quoteEventMatchesTimeframe(event, normalizedTimeframe));
  });
  if (eventWithBars) {
    return addVolumeRatios(
      historicalBarsFromEvent(eventWithBars)
        .filter(Boolean)
        .sort(compareQuotesByTime),
    );
  }
  const rawQuotes = quoteEvents
    .map((event) => quoteFromEvent(event))
    .filter(Boolean)
    .sort(compareQuotesByTime);
  const uniqueQuotes = dedupeQuotes(rawQuotes);
  if (shouldUseSnapshotCandles(rawQuotes, uniqueQuotes)) {
    return quoteSnapshotsToCandles(rawQuotes);
  }
  return uniqueQuotes;
}

export function quoteEventMatchesTimeframe(event, timeframe) {
  const data = event && event.data && typeof event.data === "object" ? event.data : {};
  const candidates = [
    data.timeframe,
    data.timeframe_label,
    data.volume_timeframe,
    data.historical_bar_size,
    data.hybrid_signal_bar_size,
  ];
  return candidates.some((value) => normalizeChartTimeframeCandidate(value) === timeframe);
}

export function normalizeChartTimeframeCandidate(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "";
  const aliases = {
    "3 mins": "3m",
    "3 min": "3m",
    "10 mins": "10m",
    "10 min": "10m",
    "15 mins": "15m",
    "15 min": "15m",
    "30 mins": "30m",
    "30 min": "30m",
    "1 hour": "1h",
    "60 mins": "1h",
    "60 min": "1h",
    "4 hours": "4h",
    "4 hour": "4h",
    "1 day": "1d",
    "1d": "1d",
  };
  const candidate = aliases[text] || text;
  return SETUP_CHART_TIMEFRAMES.some((item) => item.id === candidate) ? candidate : "";
}

export function historicalBarsFromEvent(event) {
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const bars = Array.isArray(data.historical_bars) ? data.historical_bars : [];
  return bars.map((bar) => quoteFromHistoricalBar(event, bar, data));
}

export function quoteFromHistoricalBar(event, bar, eventData) {
  if (!bar || typeof bar !== "object") return null;
  const close = firstNumber(bar.close);
  const open = firstNumber(bar.open, close);
  const high = firstNumber(bar.high, Math.max(open ?? 0, close ?? 0));
  const low = firstNumber(bar.low, Math.min(open ?? 0, close ?? 0));
  if ([open, high, low, close].some((value) => value === null)) return null;
  return {
    timestamp: bar.date || event.timestamp,
    open,
    high,
    low,
    close,
    price: close,
    bid: numberOrNull(eventData.bid),
    ask: numberOrNull(eventData.ask),
    spread: numberOrNull(eventData.spread),
    spread_bps: numberOrNull(eventData.spread_bps),
    volume: numberOrNull(bar.volume),
    bar_volume_15m: numberOrNull(eventData.bar_volume_15m),
    avg_volume_15m: numberOrNull(eventData.avg_volume_15m),
    volume_ratio_15m: numberOrNull(eventData.volume_ratio_15m),
    volume_ratio: numberOrNull(bar.volume_ratio),
    volume_ratio_closed_bar: numberOrNull(bar.volume_ratio_closed_bar),
    volume_ratio_live: numberOrNull(bar.volume_ratio_live),
    volume_status: eventData.volume_status || "",
    volume_timeframe: eventData.volume_timeframe || "",
    volume_comparison_mode: eventData.volume_comparison_mode || "",
    volume_sample_days: numberOrNull(eventData.volume_sample_days),
    average_volume_ratio_last_2_bars: numberOrNull(eventData.average_volume_ratio_last_2_bars),
    bars_above_resistance: numberOrNull(eventData.bars_above_resistance),
    minimum_tick: numberOrNull(eventData.minimum_tick),
    atr_15m: numberOrNull(eventData.atr_15m),
    atr_1h: numberOrNull(eventData.atr_1h),
    atr_1h_status: eventData.atr_1h_status || "",
    atr_1h_bar_size: eventData.atr_1h_bar_size || "",
    atr_1h_duration: eventData.atr_1h_duration || "",
    atr_1h_use_rth: eventData.atr_1h_use_rth,
    bars_required_for_atr: numberOrNull(eventData.bars_required_for_atr),
    historical_1h_available: eventData.historical_1h_available,
    historical_1h_error: eventData.historical_1h_error || "",
    last_successful_atr_1h: numberOrNull(eventData.last_successful_atr_1h),
    last_successful_atr_1h_at: eventData.last_successful_atr_1h_at || "",
    atr_1h_age_seconds: numberOrNull(eventData.atr_1h_age_seconds),
    bars_15m_count: numberOrNull(eventData.bars_15m_count),
    bars_1h_count: numberOrNull(eventData.bars_1h_count),
    market_data_source: eventData.market_data_source || "",
    live_quote_source: eventData.live_quote_source || "",
    market_data_type_requested: numberOrNull(eventData.market_data_type_requested),
    market_data_type_actual: numberOrNull(eventData.market_data_type_actual),
    live_market_data_status: eventData.live_market_data_status || "",
    last_ibkr_error_code: numberOrNull(eventData.last_ibkr_error_code),
    last_ibkr_error_message: eventData.last_ibkr_error_message || "",
    market_data_readiness: eventData.market_data_readiness || null,
    hybrid_sources: eventData.hybrid_sources || null,
    hybrid_signal_bar_size: eventData.hybrid_signal_bar_size || "",
    hybrid_atr_1h_bar_size: eventData.hybrid_atr_1h_bar_size || "",
    session: eventData.session || "",
    source: eventData.market_data_source || eventData.source || "",
    bar_date: bar.date || "",
    timeframe: eventData.timeframe || "",
    timeframe_label: eventData.timeframe_label || "",
    historical_bar_size: eventData.historical_bar_size || "",
    historical_duration: eventData.historical_duration || "",
  };
}

export function quoteFromEvent(event) {
  const data = event.data && typeof event.data === "object" ? event.data : {};
  if (data.available === false) return null;
  const close = firstNumber(data.close, data.price, data.last);
  const open = firstNumber(data.open, close);
  const high = firstNumber(data.high, Math.max(open ?? 0, close ?? 0));
  const low = firstNumber(data.low, Math.min(open ?? 0, close ?? 0));
  if ([open, high, low, close].some((value) => value === null)) return null;
  return {
    timestamp: event.timestamp,
    open,
    high,
    low,
    close,
    price: firstNumber(data.price, data.last, close),
    bid: numberOrNull(data.bid),
    ask: numberOrNull(data.ask),
    spread: numberOrNull(data.spread),
    spread_bps: numberOrNull(data.spread_bps),
    volume: numberOrNull(data.volume),
    bar_volume_15m: numberOrNull(data.bar_volume_15m),
    avg_volume_15m: numberOrNull(data.avg_volume_15m),
    volume_ratio_15m: numberOrNull(data.volume_ratio_15m),
    volume_ratio: numberOrNull(data.volume_ratio),
    volume_ratio_closed_bar: numberOrNull(data.volume_ratio_closed_bar),
    volume_ratio_live: numberOrNull(data.volume_ratio_live),
    volume_status: data.volume_status || "",
    volume_timeframe: data.volume_timeframe || "",
    volume_comparison_mode: data.volume_comparison_mode || "",
    volume_sample_days: numberOrNull(data.volume_sample_days),
    average_volume_ratio_last_2_bars: numberOrNull(data.average_volume_ratio_last_2_bars),
    bars_above_resistance: numberOrNull(data.bars_above_resistance),
    minimum_tick: numberOrNull(data.minimum_tick),
    atr_15m: numberOrNull(data.atr_15m),
    atr_1h: numberOrNull(data.atr_1h),
    atr_1h_status: data.atr_1h_status || "",
    atr_1h_bar_size: data.atr_1h_bar_size || "",
    atr_1h_duration: data.atr_1h_duration || "",
    atr_1h_use_rth: data.atr_1h_use_rth,
    bars_required_for_atr: numberOrNull(data.bars_required_for_atr),
    historical_1h_available: data.historical_1h_available,
    historical_1h_error: data.historical_1h_error || "",
    last_successful_atr_1h: numberOrNull(data.last_successful_atr_1h),
    last_successful_atr_1h_at: data.last_successful_atr_1h_at || "",
    atr_1h_age_seconds: numberOrNull(data.atr_1h_age_seconds),
    bar_count: numberOrNull(data.bar_count),
    bars_15m_count: numberOrNull(data.bars_15m_count),
    bars_1h_count: numberOrNull(data.bars_1h_count),
    market_data_source: data.market_data_source || "",
    live_quote_source: data.live_quote_source || "",
    market_data_type_requested: numberOrNull(data.market_data_type_requested),
    market_data_type_actual: numberOrNull(data.market_data_type_actual),
    live_market_data_status: data.live_market_data_status || "",
    last_ibkr_error_code: numberOrNull(data.last_ibkr_error_code),
    last_ibkr_error_message: data.last_ibkr_error_message || "",
    market_data_readiness: data.market_data_readiness || null,
    hybrid_sources: data.hybrid_sources || null,
    hybrid_signal_bar_size: data.hybrid_signal_bar_size || "",
    hybrid_atr_1h_bar_size: data.hybrid_atr_1h_bar_size || "",
    session: data.session || "",
    source: data.market_data_source || data.source || "",
    bar_date: data.bar_date || data.date || "",
    timeframe: data.timeframe || "",
    timeframe_label: data.timeframe_label || "",
    historical_bar_size: data.historical_bar_size || "",
    historical_duration: data.historical_duration || "",
  };
}

export function latestQuoteFromEvents(events) {
  const event = (events || []).find((item) => item.event_type === "stock_quote");
  return event ? quoteFromEvent(event) : null;
}

export function latestQuoteForSymbol(events, symbol) {
  const expected = String(symbol || "").toUpperCase();
  const event = (events || []).find((item) => (
    item.event_type === "stock_quote"
    && String(item.symbol || "").toUpperCase() === expected
  ));
  return event ? quoteFromEvent(event) : null;
}

export function mergeMarketSnapshots(...sources) {
  const merged = {};
  let hasValue = false;
  sources.filter(Boolean).forEach((source) => {
    Object.entries(source).forEach(([key, value]) => {
      if (isMissingMarketValue(merged[key]) && !isMissingMarketValue(value)) {
        merged[key] = value;
        hasValue = true;
      }
    });
  });
  return hasValue ? merged : null;
}

export function isMissingMarketValue(value) {
  return value === null || value === undefined || value === "";
}

export function shouldUseSnapshotCandles(rawQuotes, uniqueQuotes) {
  if (rawQuotes.length < 6) return false;
  const hasHistoricalSnapshots = rawQuotes.some((quote) => quote.source === "historical");
  if (!hasHistoricalSnapshots) return false;
  const uniqueBarDates = new Set(rawQuotes.map((quote) => quote.bar_date).filter(Boolean));
  return uniqueQuotes.length <= 2 || uniqueBarDates.size <= 1;
}

export function quoteSnapshotsToCandles(rawQuotes) {
  const seen = new Set();
  const ordered = rawQuotes.filter((quote) => {
    const key = quote.timestamp || `${quote.bar_date}:${quote.price}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return quotePrice(quote) !== null;
  });
  return addVolumeRatios(ordered.map((quote, index) => {
    const close = quotePrice(quote);
    const previousClose = index > 0 ? quotePrice(ordered[index - 1]) : close;
    const open = previousClose ?? close;
    return {
      ...quote,
      open,
      high: Math.max(open, close),
      low: Math.min(open, close),
      close,
      price: close,
      synthetic: true,
    };
  }));
}

export function dedupeQuotes(quotes) {
  const byKey = new Map();
  quotes.forEach((quote) => {
    byKey.set(quoteCandleKey(quote), quote);
  });
  return addVolumeRatios(Array.from(byKey.values()).sort(compareQuotesByTime));
}

export function quoteCandleKey(quote) {
  if (quote.bar_date) return `bar:${quote.bar_date}`;
  if (quote.source === "historical") return `historical:${quote.timestamp}`;
  return `tick:${quote.timestamp}`;
}

export function addVolumeRatios(quotes) {
  return quotes.map((quote, index) => {
    if (quote.volume_ratio !== null && quote.volume_ratio !== undefined) return quote;
    const volume = numberOrNull(quote.volume);
    if (volume === null || volume <= 0 || index === 0) return quote;
    const previousVolumes = quotes
      .slice(Math.max(0, index - 20), index)
      .map((item) => numberOrNull(item.volume))
      .filter((value) => value !== null && value > 0);
    if (!previousVolumes.length) return quote;
    const average = previousVolumes.reduce((sum, value) => sum + value, 0) / previousVolumes.length;
    if (average <= 0) return quote;
    return {
      ...quote,
      volume_ratio: volume / average,
    };
  });
}

export function compareQuotesByTime(left, right) {
  return quoteSortTime(left) - quoteSortTime(right);
}

export function quoteSortTime(quote) {
  if (!quote) return 0;
  const time = parseChartDate(quote.bar_date || quote.timestamp || 0).getTime();
  return Number.isNaN(time) ? 0 : time;
}

export function quoteVolumeRatio(quote) {
  return firstNumber(
    quote && quote.volume_ratio_15m,
    quote && quote.volume_ratio_closed_bar,
    quote && quote.volume_ratio,
  );
}

export function quotePrice(quote) {
  if (!quote) return null;
  return firstNumber(quote.price, quote.close);
}

export function parseChartDate(value) {
  const text = String(value || "").trim();
  const intraday = text.match(/^(\d{4})(\d{2})(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (intraday) {
    const [, year, month, day, hour, minute, second = "00"] = intraday;
    return new Date(`${year}-${month}-${day}T${hour}:${minute}:${second}`);
  }
  const compact = text.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) {
    const [, year, month, day] = compact;
    return new Date(`${year}-${month}-${day}T00:00:00`);
  }
  return new Date(value);
}
