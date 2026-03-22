import { useState, useCallback, useMemo } from "react";
import type { Platform } from "../../types/arbitrage";
import { useArbitrageOpportunities } from "../../hooks/useArbitrageOpportunities";
import { ScanStatus }      from "../Status/ScanStatus";
import { MarketFilter }    from "../Filters/MarketFilter";
import { OpportunityList } from "../Opportunities/OpportunityList";
import { EvEdgeList }      from "../EvEdges/EvEdgeList";
import { ValueList }       from "../Value/ValueList";
import { CryptoTab }       from "../Crypto/CryptoTab";
import { GuidePage }       from "../Guide/GuidePage";
import { Spinner }         from "../UI/Spinner";

type Tab = "guide" | "arb" | "ev" | "value" | "crypto";

interface TabDef {
  id:          Tab;
  label:       string;
  activeColor: string;
  badgeColor:  string;
  count?:      number;
}

export function Dashboard() {
  const [activeTab, setActiveTab]   = useState<Tab>("guide");
  const [isScanning, setIsScanning] = useState(false);

  // Value tab platform filter — lifted here so it survives tab switching
  const ALL_VALUE_PLATFORMS: Platform[] = ["kalshi", "polymarket"];
  const [enabledValuePlatforms, setEnabledValuePlatforms] = useState<Platform[]>(
    ["kalshi", "polymarket"]
  );
  const toggleValuePlatform = useCallback((plat: Platform) => {
    setEnabledValuePlatforms(prev => {
      if (prev.includes(plat)) {
        if (prev.length === 1) return prev; // keep at least one
        return prev.filter(p => p !== plat);
      }
      return [...prev, plat];
    });
  }, []);

  const {
    opportunities,
    evEdges,
    valueOps,
    scannerStatus,
    isConnected,
    isLoading,
    lastUpdated,
    filters,
    setFilters,
    forceScan,
    totalMarkets,
    // Crypto markets
    cryptoMarkets,
    cryptoArbCount,
    cryptoScannedAt,
    isCryptoScanning,
    cryptoScanError,
    forceCryptoScan,
  } = useArbitrageOpportunities();

  const handleForceScan = useCallback(async () => {
    setIsScanning(true);
    await forceScan();
    // Brief visual feedback — real update arrives via WebSocket
    setTimeout(() => setIsScanning(false), 3000);
  }, [forceScan]);

  const tabs: TabDef[] = [
    {
      id:          "guide",
      label:       "Guide",
      activeColor: "border-gray-400 text-gray-200 bg-gray-800/30",
      badgeColor:  "",
    },
    {
      id:          "arb",
      label:       "Guaranteed Arb",
      activeColor: "border-green-500 text-green-400 bg-green-900/10",
      badgeColor:  "bg-green-900/60 text-green-400 border-green-800/60",
      count:       opportunities.length,
    },
    {
      id:          "ev",
      label:       "EV+ Bets",
      activeColor: "border-amber-500 text-amber-400 bg-amber-900/10",
      badgeColor:  "bg-amber-900/60 text-amber-400 border-amber-800/60",
      count:       evEdges.length,
    },
    {
      id:          "value",
      label:       "Value Bets",
      activeColor: "border-blue-500 text-blue-400 bg-blue-900/10",
      badgeColor:  "bg-blue-900/60 text-blue-400 border-blue-800/60",
      count:       valueOps.length,
    },
    {
      id:          "crypto",
      label:       "Crypto Markets",
      activeColor: "border-cyan-500 text-cyan-400 bg-cyan-900/10",
      badgeColor:  "bg-cyan-900/60 text-cyan-400 border-cyan-800/60",
      count:       cryptoArbCount,
    },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-mono">
      {/* Header */}
      <header className="border-b border-gray-800 px-4 py-3 sm:px-6 sm:py-4">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4">
          {/* Title block — clicking returns to guide */}
          <button
            onClick={() => setActiveTab("guide")}
            className="text-left hover:opacity-80 transition-opacity"
          >
            <h1 className="text-lg font-bold text-white tracking-tight">
              Arb Scanner
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">
              Prediction markets &amp; sportsbook arbitrage · auto-updates every ~90s
            </p>
          </button>

          {/* Scan Now button */}
          <button
            onClick={handleForceScan}
            disabled={isScanning}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-all ${
              isScanning
                ? "bg-gray-800 border-gray-700 text-gray-500 cursor-not-allowed"
                : "bg-blue-950 border-blue-700 text-blue-300 hover:bg-blue-900 hover:border-blue-500 hover:text-blue-200 active:scale-95"
            }`}
          >
            <svg
              className={`w-3.5 h-3.5 ${isScanning ? "animate-spin" : ""}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M21 12a9 9 0 11-6.219-8.56" />
            </svg>
            {isScanning ? "Scanning…" : "Scan Now"}
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-5xl mx-auto px-3 py-4 sm:px-6 sm:py-6 space-y-4">
        <ScanStatus
          statuses={scannerStatus}
          isConnected={isConnected}
          lastUpdated={lastUpdated}
          totalMarkets={totalMarkets}
        />

        {/* Tab bar */}
        <div className="flex gap-1 border-b border-gray-800 pb-0 flex-wrap">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-2.5 py-1.5 text-xs sm:px-4 sm:py-2 sm:text-sm font-medium rounded-t transition-colors border-b-2 -mb-px ${
                activeTab === tab.id
                  ? tab.activeColor
                  : "border-transparent text-gray-500 hover:text-gray-300 hover:border-gray-600"
              }`}
            >
              {tab.label}
              {tab.count != null && tab.count > 0 && (
                <span className={`ml-2 px-1.5 py-0.5 rounded-full text-xs border ${tab.badgeColor}`}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {isLoading && activeTab !== "guide" ? (
          <Spinner />
        ) : activeTab === "guide" ? (
          <GuidePage
            onNavigate={setActiveTab}
            counts={{
              arb:    opportunities.length,
              ev:     evEdges.length,
              value:  valueOps.length,
              crypto: cryptoArbCount,
            }}
          />
        ) : activeTab === "arb" ? (
          <>
            <MarketFilter filters={filters} onChange={setFilters} />
            <OpportunityList opportunities={opportunities} />
          </>
        ) : activeTab === "ev" ? (
          <EvEdgeList edges={evEdges} />
        ) : activeTab === "value" ? (
          <ValueList
            valueOps={valueOps}
            allPlatforms={ALL_VALUE_PLATFORMS}
            enabledPlatforms={enabledValuePlatforms}
            onTogglePlatform={toggleValuePlatform}
          />
        ) : (
          <CryptoTab
            markets={cryptoMarkets}
            arbCount={cryptoArbCount}
            scannedAt={cryptoScannedAt}
            isScanning={isCryptoScanning}
            scanError={cryptoScanError}
            onScan={forceCryptoScan}
          />
        )}
      </main>
    </div>
  );
}
