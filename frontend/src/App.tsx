import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

interface CdrItem {
  symbol: string;
  name: string;
  issuer: string;
  country: string | null;
  source: string;
  source_url: string | null;
  underlying_ticker: string | null;
  underlying_symbol: string | null;
  underlying_exchange: string | null;
  underlying_currency: string | null;
  cdr_exchange: string | null;
  sector: string | null;
  ratio: number | null;
  fx_forward_rate: number | null;
  listing_date: string | null;
  volume_avg_10d: number | null;
  is_active: boolean;
  is_new: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  updated_at: string | null;
}

interface SyncReport {
  status: string;
  trigger: string;
  started_at: string;
  completed_at: string;
  table: string;
  total_scraped: number;
  total_active: number;
  added_count: number;
  updated_count: number;
  removed_count: number;
  unchanged_count: number;
  added_symbols: string[];
  updated_symbols: string[];
  removed_symbols: string[];
  source_counts: Record<string, number>;
  errors: Record<string, string>;
}

interface CdrListResponse {
  status: string;
  table: string;
  count: number;
  total_active: number;
  total_new: number;
  issuer_counts: Record<string, number>;
  last_sync: SyncReport | null;
  items: CdrItem[];
  message: string;
}

interface MarketQuote {
  symbol: string;
  questrade_symbol: string;
  symbol_id: number;
  exchange: string | null;
  currency: string;
  last_price: number | null;
  bid_price: number | null;
  ask_price: number | null;
  open_price: number | null;
  prev_close_price: number | null;
  high_price: number | null;
  low_price: number | null;
  volume: number | null;
  change: number | null;
  change_percent: number | null;
  delay_seconds: number | null;
  is_halted: boolean;
  last_trade_time: string | null;
  fetched_at: string;
}

interface BatchQuotesResponse {
  status: string;
  quotes: Record<string, MarketQuote>;
  missing: string[];
  cached_ttl_seconds: number;
}

type SortField = "symbol" | "name";
type SortOrder = "asc" | "desc";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const QUOTE_AUTO_REFRESH_MS = 60_000;

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatRatio(val: number | null): string {
  if (val === null || val === undefined) return "—";
  return val.toFixed(4);
}

function formatVolume(val: number | null): string {
  if (val === null || val === undefined) return "—";
  return val.toLocaleString();
}

function formatPrice(val: number | null | undefined): string {
  if (val === null || val === undefined) return "—";
  return `$${val.toFixed(2)}`;
}

export default function App() {
  const [data, setData] = useState<CdrListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncBanner, setSyncBanner] = useState<string | null>(null);

  const [quotes, setQuotes] = useState<Record<string, MarketQuote>>({});
  const [quotesLoading, setQuotesLoading] = useState(false);
  const [quotesError, setQuotesError] = useState<string | null>(null);
  const [quotesUpdatedAt, setQuotesUpdatedAt] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const lastQuotesFetchRef = useRef<number>(0);

  const [search, setSearch] = useState("");
  const [issuerFilter, setIssuerFilter] = useState<string>("ALL");
  const [countryFilter, setCountryFilter] = useState<string>("ALL");
  const [newOnly, setNewOnly] = useState(false);
  const [letterFilter, setLetterFilter] = useState<string>("ALL");
  const [sortField, setSortField] = useState<SortField>("symbol");
  const [sortOrder, setSortOrder] = useState<SortOrder>("asc");
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);

  const fetchQuotes = useCallback(async (refresh = false) => {
    setQuotesLoading(true);
    setQuotesError(null);
    try {
      const qs = refresh ? "?refresh=true" : "";
      const res = await fetch(`/api/market-data${qs}`);
      if (!res.ok) throw new Error(`Quotes HTTP ${res.status}`);
      const json: BatchQuotesResponse = await res.json();
      setQuotes(json.quotes || {});
      lastQuotesFetchRef.current = Date.now();
      setQuotesUpdatedAt(new Date().toISOString());
    } catch (err) {
      setQuotesError(
        err instanceof Error ? err.message : "Live Questrade quotes unavailable."
      );
    } finally {
      setQuotesLoading(false);
    }
  }, []);

  const fetchSingleQuote = useCallback(
    async (symbol: string, issuer?: string) => {
      if (quotes[symbol]) return;
      try {
        const params = new URLSearchParams();
        if (issuer) params.set("issuer", issuer);
        const qs = params.toString() ? `?${params.toString()}` : "";
        const res = await fetch(`/api/market-data/${encodeURIComponent(symbol)}${qs}`);
        if (!res.ok) return;
        const json: { quote?: MarketQuote } = await res.json();
        if (json.quote) {
          setQuotes((prev) => ({ ...prev, [symbol]: json.quote! }));
        }
      } catch {
        // Ignore single-quote fallback failures cleanly
      }
    },
    [quotes]
  );

  const fetchCdrs = useCallback(async (refresh = false) => {
    setError(null);
    try {
      const qs = refresh ? "?refresh=true" : "";
      const res = await fetch(`/api/cdrs${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: CdrListResponse = await res.json();
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load CDR directory.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchCdrs();
    void fetchQuotes(false);
  }, [fetchCdrs, fetchQuotes]);

  useEffect(() => {
    if (!autoRefresh) return;

    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void fetchQuotes(true);
      }
    }, QUOTE_AUTO_REFRESH_MS);

    const handleVisibilityChange = () => {
      if (
        document.visibilityState === "visible" &&
        Date.now() - lastQuotesFetchRef.current >= QUOTE_AUTO_REFRESH_MS
      ) {
        void fetchQuotes(true);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [autoRefresh, fetchQuotes]);

  const handleSyncNow = async () => {
    setSyncing(true);
    setError(null);
    setSyncBanner(null);
    try {
      const res = await fetch("/api/cdrs/sync?trigger=manual", { method: "POST" });
      if (!res.ok) throw new Error(`Sync failed (HTTP ${res.status})`);
      const report: SyncReport = await res.json();
      await fetchCdrs(true);
      void fetchQuotes(true);
      setSyncBanner(
        `Synced ${report.total_scraped} CDRs (${report.added_count} added, ${report.updated_count} updated, ${report.removed_count} removed).`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sync CDR sources.");
    } finally {
      setSyncing(false);
    }
  };

  const countries = useMemo(() => {
    const items = data?.items ?? [];
    const set = new Set<string>();
    for (const item of items) {
      if (item.country) set.add(item.country);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [data]);

  const baseFilteredItems = useMemo(() => {
    const items = data?.items ?? [];
    const term = search.trim().toLowerCase();

    return items.filter((item) => {
      if (issuerFilter !== "ALL" && item.issuer !== issuerFilter) return false;
      if (countryFilter !== "ALL" && (item.country ?? "") !== countryFilter) return false;
      if (newOnly && !item.is_new) return false;
      if (term) {
        const haystack = [
          item.symbol,
          item.name,
          item.sector ?? "",
          item.underlying_ticker ?? "",
          item.country ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }, [data, issuerFilter, countryFilter, newOnly, search]);

  const availableLetters = useMemo(() => {
    const set = new Set<string>();
    for (const item of baseFilteredItems) {
      const target = sortField === "symbol" ? item.symbol : item.name;
      const firstChar = target.trim().charAt(0).toUpperCase();
      if (firstChar >= "A" && firstChar <= "Z") {
        set.add(firstChar);
      }
    }
    return set;
  }, [baseFilteredItems, sortField]);

  const displayedItems = useMemo(() => {
    const filtered =
      letterFilter === "ALL"
        ? baseFilteredItems
        : baseFilteredItems.filter((item) => {
            const target = sortField === "symbol" ? item.symbol : item.name;
            return target.trim().charAt(0).toUpperCase() === letterFilter;
          });

    return [...filtered].sort((a, b) => {
      const left = (sortField === "symbol" ? a.symbol : a.name).trim();
      const right = (sortField === "symbol" ? b.symbol : b.name).trim();
      const cmp = left.localeCompare(right, undefined, { sensitivity: "base" });
      return sortOrder === "asc" ? cmp : -cmp;
    });
  }, [baseFilteredItems, letterFilter, sortField, sortOrder]);

  const totalActive = data?.total_active ?? 0;
  const cibcCount = data?.issuer_counts?.CIBC ?? 0;
  const bmoCount = data?.issuer_counts?.BMO ?? 0;
  const newCount = data?.total_new ?? 0;

  const resetAllFilters = () => {
    setSearch("");
    setIssuerFilter("ALL");
    setCountryFilter("ALL");
    setNewOnly(false);
    setLetterFilter("ALL");
  };

  return (
    <main className="page">
      <header className="header">
        <div>
          <p className="eyebrow">Canadian Depositary Receipts</p>
          <h1 className="title">CDR Directory &amp; Update Tracker</h1>
          <p className="subtitle">
            Alphabetical registry synced from CIBC &amp; BMO directories into BigQuery.
          </p>
        </div>

        <div className="header-actions">
          <div className="sync-meta">
            <span>
              Last sync:{" "}
              <strong>
                {data?.last_sync?.completed_at
                  ? formatTimestamp(data.last_sync.completed_at)
                  : "Startup / Cached"}
              </strong>
            </span>
            <span>
              Quotes:{" "}
              <strong>{quotesUpdatedAt ? formatTimestamp(quotesUpdatedAt) : "Loading..."}</strong>{" "}
              ({autoRefresh ? "Auto 60s" : "Manual"})
            </span>
            {data?.last_sync?.trigger && <span>Trigger: {data.last_sync.trigger}</span>}
            {quotesError && <span className="quote-neg">Quotes: {quotesError}</span>}
          </div>
          <label className="auto-toggle" title="Automatically refresh Questrade quotes every 60 seconds while this page is open">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <span>Auto 60s</span>
          </label>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void fetchQuotes(true)}
            disabled={quotesLoading}
            title="Fetch live Questrade quotes on demand"
            aria-label="Fetch live Questrade quotes on demand"
          >
            <svg
              className={`refresh-icon ${quotesLoading ? "spinning" : ""}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.1"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <polyline points="21 3 21 9 15 9" />
            </svg>
            <span>{quotesLoading ? "Refreshing..." : "Live Quote"}</span>
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSyncNow}
            disabled={syncing}
          >
            {syncing && <span className="spinner" aria-hidden="true" />}
            {syncing ? "Syncing..." : "Sync Now"}
          </button>
        </div>
      </header>

      {error && (
        <div className="banner banner-error" role="alert">
          <span>Error: {error}</span>
          <button
            type="button"
            className="banner-close"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}

      {syncBanner && (
        <div className="banner banner-info" role="status">
          <span>{syncBanner}</span>
          <button
            type="button"
            className="banner-close"
            onClick={() => setSyncBanner(null)}
            aria-label="Dismiss sync notice"
          >
            ×
          </button>
        </div>
      )}

      <section className="kpi-grid" aria-label="CDR Summary">
        <button
          type="button"
          className={`kpi-card ${issuerFilter === "ALL" && !newOnly ? "active" : ""}`}
          onClick={() => {
            setIssuerFilter("ALL");
            setNewOnly(false);
          }}
        >
          <span className="kpi-label">Total Active CDRs</span>
          <span className="kpi-value">{loading ? "—" : totalActive}</span>
          <span className="kpi-sub">All tracked issuers</span>
        </button>

        <button
          type="button"
          className={`kpi-card ${issuerFilter === "CIBC" && !newOnly ? "active" : ""}`}
          onClick={() => {
            setIssuerFilter((prev) => (prev === "CIBC" ? "ALL" : "CIBC"));
            setNewOnly(false);
          }}
        >
          <span className="kpi-label">CIBC Issued</span>
          <span className="kpi-value">{loading ? "—" : cibcCount}</span>
          <span className="kpi-sub">cdr.cibc.com directory</span>
        </button>

        <button
          type="button"
          className={`kpi-card ${issuerFilter === "BMO" && !newOnly ? "active" : ""}`}
          onClick={() => {
            setIssuerFilter((prev) => (prev === "BMO" ? "ALL" : "BMO"));
            setNewOnly(false);
          }}
        >
          <span className="kpi-label">BMO Issued</span>
          <span className="kpi-value">{loading ? "—" : bmoCount}</span>
          <span className="kpi-sub">bmoinvestorline.com directory</span>
        </button>

        <button
          type="button"
          className={`kpi-card ${newOnly ? "active" : ""}`}
          onClick={() => setNewOnly((prev) => !prev)}
        >
          <span className="kpi-label">Newly Added (7d)</span>
          <span className="kpi-value">{loading ? "—" : newCount}</span>
          <span className="kpi-sub">Captured after baseline</span>
        </button>
      </section>

      <section className="toolbar-card" aria-label="Search and Alphabetical Controls">
        <div className="filters-row">
          <div className="search-wrap">
            <input
              type="search"
              className="search-input"
              placeholder="Search ticker, company name, underlying symbol, or sector..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search CDRs"
            />
            {search && (
              <button
                type="button"
                className="search-clear"
                onClick={() => setSearch("")}
                aria-label="Clear search"
              >
                ×
              </button>
            )}
          </div>

          <div className="segmented" role="group" aria-label="Filter by issuer">
            {(["ALL", "CIBC", "BMO"] as const).map((iss) => (
              <button
                key={iss}
                type="button"
                className={`seg-btn ${issuerFilter === iss ? "active" : ""}`}
                onClick={() => setIssuerFilter(iss)}
              >
                {iss === "ALL" ? "All Issuers" : iss}
              </button>
            ))}
          </div>

          <select
            className="select-input"
            value={countryFilter}
            onChange={(e) => setCountryFilter(e.target.value)}
            aria-label="Filter by country"
          >
            <option value="ALL">All Countries</option>
            {countries.map((country) => (
              <option key={country} value={country}>
                {country}
              </option>
            ))}
          </select>

          <button
            type="button"
            className={`toggle-pill ${newOnly ? "active" : ""}`}
            onClick={() => setNewOnly((prev) => !prev)}
          >
            New Only ({newCount})
          </button>

          <div className="segmented" role="group" aria-label="Sort field">
            <button
              type="button"
              className={`seg-btn ${sortField === "symbol" ? "active" : ""}`}
              onClick={() => {
                if (sortField === "symbol") {
                  setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
                } else {
                  setSortField("symbol");
                  setSortOrder("asc");
                  setLetterFilter("ALL");
                }
              }}
            >
              Symbol {sortField === "symbol" ? (sortOrder === "asc" ? "A–Z" : "Z–A") : "A–Z"}
            </button>
            <button
              type="button"
              className={`seg-btn ${sortField === "name" ? "active" : ""}`}
              onClick={() => {
                if (sortField === "name") {
                  setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
                } else {
                  setSortField("name");
                  setSortOrder("asc");
                  setLetterFilter("ALL");
                }
              }}
            >
              Name {sortField === "name" ? (sortOrder === "asc" ? "A–Z" : "Z–A") : "A–Z"}
            </button>
          </div>
        </div>

        <div className="az-rail" role="navigation" aria-label="Alphabetical Filter">
          <span className="az-label">
            {sortField === "symbol" ? "Symbol" : "Name"} Index:
          </span>
          <button
            type="button"
            className={`az-btn ${letterFilter === "ALL" ? "active" : ""}`}
            onClick={() => setLetterFilter("ALL")}
          >
            ALL
          </button>
          {ALPHABET.map((letter) => {
            const hasItems = availableLetters.has(letter);
            return (
              <button
                key={letter}
                type="button"
                className={`az-btn ${letterFilter === letter ? "active" : ""}`}
                disabled={!hasItems}
                onClick={() =>
                  setLetterFilter((prev) => (prev === letter ? "ALL" : letter))
                }
              >
                {letter}
              </button>
            );
          })}
        </div>
      </section>

      <section className="table-card" aria-label="CDR Directory Table">
        {loading ? (
          <div className="empty-state">Loading CDR directory from BigQuery...</div>
        ) : displayedItems.length === 0 ? (
          <div className="empty-state">
            <p>No CDRs match your current filters.</p>
            {(search || issuerFilter !== "ALL" || countryFilter !== "ALL" || newOnly || letterFilter !== "ALL") && (
              <button type="button" className="btn-ghost" onClick={resetAllFilters}>
                Reset filters
              </button>
            )}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="cdr-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Company Name</th>
                  <th>Issuer</th>
                  <th>Country</th>
                  <th>Sector</th>
                  <th className="num">Price (CAD)</th>
                  <th className="num">Ratio</th>
                  <th className="num">Listing Date</th>
                </tr>
              </thead>
              <tbody>
                {displayedItems.map((item) => {
                  const isExpanded = expandedSymbol === item.symbol;
                  const issuerClass =
                    item.issuer === "CIBC" ? "badge-cibc" : "badge-bmo";
                  const quote = quotes[item.symbol];
                  const pct = quote?.change_percent;
                  const pctClass =
                    pct == null
                      ? "quote-flat"
                      : pct > 0
                        ? "quote-pos"
                        : pct < 0
                          ? "quote-neg"
                          : "quote-flat";

                  return (
                    <Fragment key={item.symbol}>
                      <tr
                        className={`cdr-row ${isExpanded ? "expanded" : ""}`}
                        onClick={() => {
                          const nextExpanded = isExpanded ? null : item.symbol;
                          setExpandedSymbol(nextExpanded);
                          if (nextExpanded) {
                            void fetchSingleQuote(item.symbol, item.issuer);
                          }
                        }}
                      >
                        <td>
                          <div className="symbol-cell">
                            <span className="ticker">{item.symbol}</span>
                            {item.is_new && <span className="badge badge-new">NEW</span>}
                          </div>
                        </td>
                        <td>
                          <div className="name-main">{item.name}</div>
                          {(item.underlying_ticker || item.underlying_exchange) && (
                            <div className="name-sub">
                              {[item.underlying_ticker, item.underlying_exchange]
                                .filter(Boolean)
                                .join(" · ")}
                            </div>
                          )}
                        </td>
                        <td>
                          <span className={`badge ${issuerClass}`}>{item.issuer}</span>
                        </td>
                        <td>{item.country || "—"}</td>
                        <td>{item.sector || "—"}</td>
                        <td className="num">
                          <div className="quote-price">
                            {quote
                              ? formatPrice(quote.last_price)
                              : quotesLoading
                                ? "..."
                                : "—"}
                          </div>
                          {quote && pct != null && (
                            <div className={`quote-sub ${pctClass}`}>
                              {pct > 0 ? `+${pct.toFixed(2)}%` : `${pct.toFixed(2)}%`}
                            </div>
                          )}
                        </td>
                        <td className="num">{formatRatio(item.ratio)}</td>
                        <td className="num">{item.listing_date || "—"}</td>
                      </tr>

                      {isExpanded && (
                        <tr className="detail-row">
                          <td colSpan={8}>
                            <div className="detail-grid">
                              <div>
                                <span className="detail-item-label">Underlying Symbol</span>
                                <span className="detail-item-val mono">
                                  {item.underlying_symbol || item.underlying_ticker || "—"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">CDR Exchange</span>
                                <span className="detail-item-val">
                                  {item.cdr_exchange || quote?.exchange || "Cboe / TSX"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Questrade Symbol</span>
                                <span className="detail-item-val mono">
                                  {quote?.questrade_symbol || "—"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Bid / Ask</span>
                                <span className="detail-item-val mono">
                                  {quote
                                    ? `${formatPrice(quote.bid_price)} / ${formatPrice(quote.ask_price)}`
                                    : "—"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Day High / Low</span>
                                <span className="detail-item-val mono">
                                  {quote
                                    ? `${formatPrice(quote.high_price)} / ${formatPrice(quote.low_price)}`
                                    : "—"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Today Volume</span>
                                <span className="detail-item-val mono">
                                  {formatVolume(quote?.volume)}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">FX Forward Rate</span>
                                <span className="detail-item-val mono">
                                  {item.fx_forward_rate ? item.fx_forward_rate.toFixed(4) : "—"}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">10d Avg Volume</span>
                                <span className="detail-item-val mono">
                                  {formatVolume(item.volume_avg_10d)}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">First Captured</span>
                                <span className="detail-item-val">
                                  {formatTimestamp(item.first_seen_at)}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Last Verified</span>
                                <span className="detail-item-val">
                                  {formatTimestamp(item.last_seen_at)}
                                </span>
                              </div>
                              <div>
                                <span className="detail-item-label">Source</span>
                                {item.source_url ? (
                                  <a
                                    href={item.source_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="detail-link"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    {item.source === "cibc_directory"
                                      ? "CIBC Directory ↗"
                                      : "BMO Directory ↗"}
                                  </a>
                                ) : (
                                  <span className="detail-item-val">{item.source}</span>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <footer className="table-footer">
          <span>
            Showing <strong>{displayedItems.length}</strong> of{" "}
            <strong>{totalActive}</strong> active CDRs (sorted alphabetically by{" "}
            {sortField} {sortOrder.toUpperCase()})
          </span>
        </footer>
      </section>
    </main>
  );
}
