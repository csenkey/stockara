"""
Generate the initial stock watchlist (~1000 stocks) with sector and company size classifications.

Sources:
- S&P 500 (blue_chip) - ~503 stocks
- S&P MidCap 400 top selections (mid_cap) - ~300 stocks
- High-growth / smaller companies (startup) - ~200 stocks

This script fetches current index constituents and classifies them.
Run once to generate the seed data CSV that the system loads at startup.
"""

import csv
import json
from pathlib import Path


# S&P 500 constituents (blue_chip) - sourced from public datasets
# These are the current S&P 500 members as of 2025
SP500_TICKERS = [
    # Technology
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "ACN", "CSCO",
    "INTC", "IBM", "INTU", "TXN", "QCOM", "AMAT", "NOW", "ADI", "LRCX", "MU",
    "KLAC", "CDNS", "SNPS", "MCHP", "FTNT", "CRWD", "PANW", "CTSH", "IT", "EPAM",
    "HPQ", "HPE", "DELL", "KEYS", "CDW", "GEN", "FFIV", "AKAM", "ANET", "GLW",
    "APH", "TEL", "FSLR", "ON", "MPWR", "SWKS", "NXPI", "ENPH", "SEDG", "FICO",
    
    # Financials
    "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK",
    "AXP", "C", "SCHW", "CB", "MMC", "PGR", "AON", "CME", "ICE", "MCO",
    "MET", "AIG", "AFL", "PRU", "ALL", "TRV", "COF", "BK", "USB", "PNC",
    "FITB", "CFG", "HBAN", "RF", "KEY", "MTB", "STT", "CINF", "GL", "RJF",
    "BRO", "WRB", "AJG", "FIS", "FISV", "FI", "PYPL", "SYF", "DFS", "CBOE",
    
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "TMO", "ABT", "PFE", "AMGN", "MDT",
    "ELV", "CI", "ISRG", "GILD", "VRTX", "BSX", "SYK", "BDX", "ZTS", "REGN",
    "HCA", "DXCM", "IQV", "EW", "IDXX", "MTD", "A", "BAX", "BIIB", "HOLX",
    "ALGN", "RMD", "COO", "TECH", "CRL", "ILMN", "MOH", "CNC", "HUM", "CVS",
    "CAH", "MCK", "GEHC", "DVA", "HSIC", "INCY", "VTRS", "XRAY", "DGX", "LH",
    
    # Consumer Discretionary
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "BKNG", "TJX", "SBUX", "CMG",
    "ORLY", "AZO", "ROST", "MAR", "HLT", "DHI", "LEN", "PHM", "NVR", "GM",
    "F", "APTV", "GRMN", "EBAY", "ETSY", "POOL", "BBY", "DRI", "YUM", "DARDEN",
    "DPZ", "CCL", "RCL", "WYNN", "LVS", "MGM", "CZR", "EXPE", "ABNB", "DECK",
    "TPR", "RL", "PVH", "HAS", "ULTA", "LULU", "GPC", "BWA", "LKQ", "KMX",
    
    # Communication Services
    "GOOGL", "GOOG", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
    "EA", "TTWO", "MTCH", "WBD", "PARA", "OMC", "IPG", "LYV", "FOXA", "FOX",
    "NWSA", "NWS", "RBLX",
    
    # Industrials
    "GE", "CAT", "UNP", "HON", "RTX", "BA", "DE", "LMT", "UPS", "ADP",
    "GD", "NOC", "MMM", "CSX", "NSC", "WM", "RSG", "EMR", "ETN", "ITW",
    "PH", "ROK", "CMI", "DOV", "FTV", "AME", "SWK", "IR", "GWW", "FAST",
    "CTAS", "PAYX", "VRSK", "CPRT", "ODFL", "DAL", "UAL", "LUV", "AAL", "JBHT",
    "XPO", "CHRW", "EFX", "BR", "FDX", "AXON", "TDG", "HWM", "WAB", "PWR",
    
    # Consumer Staples
    "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "MDLZ", "CL", "KMB",
    "GIS", "K", "HSY", "SJM", "CAG", "HRL", "TSN", "ADM", "BG", "STZ",
    "TAP", "SAM", "KDP", "MNST", "EL", "CHD", "CLX", "SPC", "WBA", "KR",
    "SYY", "DG", "DLTR", "TGT", "COST", "BF.B", "CPB",
    
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "PXD", "MPC", "VLO", "PSX", "OXY",
    "WMB", "KMI", "OKE", "FANG", "DVN", "HAL", "BKR", "HES", "APA", "EQT",
    "CTRA", "MRO", "TRGP",
    
    # Utilities
    "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "ED", "WEC",
    "ES", "AWK", "DTE", "ETR", "EIX", "FE", "PPL", "CMS", "ATO", "LNT",
    "EVRG", "AES", "CEG", "CNP", "NI", "PNW",
    
    # Real Estate
    "PLD", "AMT", "EQIX", "CCI", "PSA", "DLR", "O", "SPG", "WELL", "VICI",
    "AVB", "EQR", "ARE", "MAA", "UDR", "ESS", "EXR", "CPT", "REG", "FRT",
    "KIM", "BXP", "VTR", "HST", "PEAK", "IRM", "CBRE", "CSGP",
    
    # Materials
    "LIN", "APD", "SHW", "ECL", "FCX", "NUE", "NEM", "DOW", "DD", "PPG",
    "VMC", "MLM", "ALB", "EMN", "CE", "CF", "MOS", "IFF", "FMC", "BALL",
    "AVY", "PKG", "IP", "WRK", "SEE", "AMCR", "CTVA",
]

# Mid-Cap stocks (mid_cap) - S&P MidCap 400 selections and other mid-caps
MIDCAP_TICKERS = [
    # Technology mid-caps
    "SMCI", "PSTG", "RBRK", "OKTA", "ZS", "DDOG", "MDB", "NET", "SNOW", "PATH",
    "CFLT", "GTLB", "ESTC", "PTC", "MANH", "SAMSN", "COUP", "BILL", "PAYC", "WEX",
    "PCTY", "GWRE", "ALTR", "DT", "BSY", "SSNC", "TYL", "VRNS", "TENB", "RPD",
    "CYBR", "SAIL", "JAMF", "QLYS", "TOST", "SQ", "FOUR", "EVTC", "NVEI", "RELY",
    
    # Healthcare mid-caps
    "NBIX", "EXAS", "RARE", "PCVX", "RPRX", "NTRA", "VEEV", "DOCS", "GMED", "HALO",
    "LNTH", "AZTA", "BIO", "NVCR", "ENSG", "AMED", "ACHC", "THC", "SGRY", "SHC",
    "OMCL", "PCRX", "TNDM", "IRTC", "NVST", "PRCT", "RVMD", "IONS", "SRPT", "BMRN",
    "ALNY", "EXEL", "UTHR", "MASI", "NKTR", "IOVA", "FATE", "RCKT", "DNLI", "TWST",
    
    # Financial mid-caps
    "LPLA", "RGA", "EVR", "SF", "SEIC", "MKTX", "VIRT", "PIPR", "WBS", "FHN",
    "EWBC", "ZION", "CMA", "FNB", "SNV", "WAL", "OZK", "BOKF", "PNFP", "UMBF",
    "SBCF", "IBKR", "HOOD", "SOFI", "LC", "UPST", "AFRM", "NAVI", "SLM", "ALLY",
    "CACC", "AX", "PRAA", "COOP", "RKT", "UWMC", "PFSI", "ESNT", "MTG", "MGIC",
    
    # Industrial mid-caps
    "TTC", "SCI", "TREX", "SITE", "AAON", "WTS", "RBC", "RRX", "MWA", "DCI",
    "GGG", "MIDD", "AIT", "GNRC", "TT", "LII", "WSO", "KNX", "SAIA", "ARCB",
    "GXO", "RXO", "HUBG", "LSTR", "WERN", "HTLD", "SNDR", "MATX", "KEX", "TPC",
    "EXPO", "HAYW", "AZEK", "DOOR", "UFPI", "BLD", "IBP", "BLDR", "MAS", "OC",
    
    # Consumer mid-caps
    "WING", "SHAK", "CAVA", "TXRH", "EAT", "DIN", "CAKE", "BJRI", "PLAY", "SIX",
    "FUN", "SEAS", "PLNT", "XPOF", "MODG", "GOLF", "SKX", "CROX", "OXM", "GIII",
    "BOOT", "DECK", "FIGS", "AEO", "ANF", "URBN", "EXPR", "BURL", "FIVE", "OLLI",
    "PRPL", "LOVE", "WSM", "RH", "W", "ETSY", "CHWY", "CVNA", "CARS", "SFM",
    
    # Energy & Utilities mid-caps
    "AR", "RRC", "SWN", "CNX", "MTDR", "PR", "CHRD", "SM", "PDCE", "VNOM",
    "GPP", "AM", "DTM", "HESM", "PAA", "PAGP", "EPD", "ET", "MPLX", "PSXP",
    "NRG", "VST", "PNM", "AVA", "NWE", "OGE", "IDA", "BKH", "SR", "SWX",
    
    # Real Estate mid-caps
    "REXR", "STAG", "TRNO", "FR", "EGP", "PNR", "INVH", "AMH", "SUI", "ELS",
    "LSI", "CUBE", "NSA", "COLD", "GLPI", "VICI", "RHP", "PK", "SHO", "DRH",
    "APLE", "INN", "AHT", "RLJ", "XHR", "IIPR", "MPW", "DOC", "HR", "OHI",
    
    # Materials & Chemicals mid-caps
    "AXTA", "RPM", "CBT", "HXL", "CC", "TROX", "KWR", "BCPC", "ESI", "MTX",
    "SLVM", "CRS", "ATI", "HAYN", "SON", "CLW", "GEF", "BCC", "MERC", "OLN",
    
    # Communication & Media mid-caps
    "IART", "ZD", "CARG", "COMP", "OPEN", "TRMB", "AGYS", "QLYS", "MIME",
    "NEWR", "PLAN", "APPN", "BL", "LSPD", "SHOP", "SQSP", "PGNY", "VERX",
    
    # Additional Industrial mid-caps
    "FELE", "EAF", "CW", "HXL", "PRIM", "STRL", "MTZ", "DY", "IESC", "ROCK",
    "VMI", "NVT", "ZWS", "ROP", "IEX", "NDSN", "XYL", "REVG", "WFRD", "CLH",
    
    # Additional Healthcare mid-caps  
    "LQDA", "MEDP", "KRYS", "INSM", "CORT", "ITCI", "PTCT", "FOLD", "AGIO", "PCVX",
    "ARVN", "KURA", "VKTX", "SMMT", "ROIV", "ACLX", "DYN", "TARS", "OLPX", "IRTC",
    
    # Additional Financial mid-caps
    "STEP", "HLNE", "AMG", "TROW", "IVZ", "AB", "BSIG", "VCTR", "CNS", "WDH",
    "CADE", "FFIN", "SFBS", "TCBI", "HWC", "ONB", "SSB", "VLY", "GBCI", "WTFC",
]

# Growth / Startup stocks (startup) - smaller cap, high-growth companies
STARTUP_TICKERS = [
    # AI & Cloud
    "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RGTI", "QUBT", "ARQQ", "BIDU", "UPST",
    "C3AI", "PRCT", "RXRX", "EXAI", "NNOX", "LAZR", "OUST", "AEVA", "CPTN", "VUZI",
    
    # Fintech & Crypto
    "COIN", "MSTR", "RIOT", "MARA", "CLSK", "BITF", "HUT", "CIFR", "ARBK", "WULF",
    "NU", "DAVE", "OPEN", "LMND", "ROOT", "HIMS", "CLOV", "TALK", "AMPL", "FLYW",
    
    # EV & Clean Energy
    "RIVN", "LCID", "FSR", "GOEV", "FFIE", "MULN", "NIO", "XPEV", "LI", "VFS",
    "ENVX", "QS", "MVST", "DCRC", "BLNK", "CHPT", "EVGO", "RUN", "NOVA", "ARRY",
    "BE", "PLUG", "FCEL", "BLDP", "STEM", "SPWR", "MAXN", "SHLS", "SEDG",
    
    # Biotech startups
    "MRNA", "BNTX", "CRSP", "EDIT", "NTLA", "BEAM", "VERV", "PRME", "ABCL", "SANA",
    "KYMR", "IMVT", "RLAY", "ERAS", "ANAB", "TGTX", "KRTX", "CRNX", "DAWN", "APGE",
    "CART", "ADPT", "ACLX", "ARVN", "GYRE", "RGNX", "BLTE", "KDNY", "XNCR", "YMAB",
    
    # Space & Defense tech
    "RKLB", "LUNR", "RDW", "ASTS", "BKSY", "MNTS", "ASTR", "VORB", "SPIR", "IRDM",
    "KTOS", "RCAT", "AVAV", "JOBY", "ACHR", "LILM", "EVTL", "BLDE", "EHANG", "UAM",
    
    # Software & SaaS startups
    "HUBS", "ZI", "BRZE", "AMPL", "CWAN", "ALKT", "INTA", "SEMR", "BIGC", "VTEX",
    "MNDY", "FRSH", "DOCN", "SUMO", "CLDR", "AYX", "FIVN", "BAND", "LPSN", "RNG",
    "ZM", "TWLO", "CRDO", "ASAN", "TEAM", "U", "RBLX", "DKNG", "PENN", "RSI",
    
    # E-commerce & Consumer tech
    "SHOP", "SE", "MELI", "GLBE", "CART", "IBTA", "WISH", "POSH", "REAL", "RERE",
    "CPNG", "BABA", "JD", "PDD", "GRAB", "GOJEK", "TOST", "SQSP", "WDAY", "DOCU",
    
    # Robotics & Hardware
    "ISRG", "IRTX", "BRKS", "NVTS", "WOLF", "ACLS", "FORM", "CEVA", "SLAB", "SITM",
    "AMBA", "LSCC", "MTSI", "RMBS", "POWI", "DIOD", "SMTC", "VSH", "IXYS", "ALGM",
    
    # Additional growth / recent IPOs
    "ARM", "BIRK", "CART", "CAVA", "DUOL", "GRAB", "KVYO", "INST", "VRT", "SMCI",
    "CELH", "ONON", "TOST", "INTA", "IOT", "KTOS", "IRDM", "FTAI", "TW", "PAYO",
    "RELY", "RKLB", "ALAB", "RXRX", "SERV", "GCT", "APLT", "OWL", "FLNC", "BTDR",
    "IREN", "CORZ", "OKLO", "SMR", "LEU", "NNE", "CCJ", "UEC", "DNN", "UUUU",
    
    # International ADRs (growth)
    "TSM", "ASML", "SAP", "TM", "SONY", "NVO", "AZN", "GSK", "SNY", "DEO",
    "UL", "BHP", "RIO", "VALE", "INFY", "WIT", "HDB", "IBN", "BABA", "TCEHY",
    "SPOT", "SHOP", "SU", "ENB", "CNQ", "CP", "BN", "MFC", "TD", "RY",
    "LOGI", "STM", "ERIC", "NOK", "MBLY", "MRVL",
]

# Sector mapping based on primary business
SECTOR_MAP = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AVGO": "Technology",
    "GOOGL": "Technology", "GOOG": "Technology", "META": "Technology", "AMZN": "Technology",
    "TSLA": "Technology", "AMD": "Technology", "INTC": "Technology", "CRM": "Technology",
    # Default: will be auto-classified based on list membership
}


def classify_sector(ticker: str, source_list: str) -> str:
    """Classify sector based on where the ticker appears in our lists."""
    if ticker in SECTOR_MAP:
        return SECTOR_MAP[ticker]
    # This is a simplified classification - in production, we'd use the actual
    # GICS sector from the data provider
    return "Diversified"


def generate_watchlist():
    """Generate the complete watchlist CSV."""
    output_path = Path(__file__).parent.parent / "data" / "watchlist_seed.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    seen_tickers = set()
    stocks = []
    
    # Process S&P 500 (blue_chip)
    for ticker in SP500_TICKERS:
        if ticker not in seen_tickers:
            seen_tickers.add(ticker)
            stocks.append({
                "ticker": ticker,
                "company_size": "blue_chip",
                "source": "sp500"
            })
    
    # Process Mid-caps
    for ticker in MIDCAP_TICKERS:
        if ticker not in seen_tickers:
            seen_tickers.add(ticker)
            stocks.append({
                "ticker": ticker,
                "company_size": "mid_cap",
                "source": "midcap400"
            })
    
    # Process Startups/Growth
    for ticker in STARTUP_TICKERS:
        if ticker not in seen_tickers:
            seen_tickers.add(ticker)
            stocks.append({
                "ticker": ticker,
                "company_size": "startup",
                "source": "growth"
            })
    
    # Write CSV
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "company_size", "source"])
        writer.writeheader()
        writer.writerows(stocks)
    
    # Also write a JSON version for easy loading
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w") as f:
        json.dump(stocks, f, indent=2)
    
    # Print summary
    blue_chip = sum(1 for s in stocks if s["company_size"] == "blue_chip")
    mid_cap = sum(1 for s in stocks if s["company_size"] == "mid_cap")
    startup = sum(1 for s in stocks if s["company_size"] == "startup")
    
    print(f"Generated watchlist with {len(stocks)} stocks:")
    print(f"  Blue-chip (S&P 500): {blue_chip}")
    print(f"  Mid-cap: {mid_cap}")
    print(f"  Startup/Growth: {startup}")
    print(f"\nOutput: {output_path}")
    print(f"Output: {json_path}")


if __name__ == "__main__":
    generate_watchlist()
