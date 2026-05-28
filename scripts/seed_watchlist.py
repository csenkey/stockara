"""Seed the stocks watchlist from data/watchlist_seed.csv.

This script loads the seed watchlist CSV into the stocks table.
Since the CSV does not include company_name or sector, this script:
- Uses the ticker as a placeholder company name
- Assigns a sector based on well-known ticker-to-sector mappings
- Falls back to "Technology" for unmapped tickers

Usage:
    python -m scripts.seed_watchlist
"""

import csv
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# Sector mappings for well-known tickers
SECTOR_MAP = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AVGO": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "AMD": "Technology", "ADBE": "Technology",
    "ACN": "Technology", "CSCO": "Technology", "INTC": "Technology", "IBM": "Technology",
    "INTU": "Technology", "TXN": "Technology", "QCOM": "Technology", "AMAT": "Technology",
    "NOW": "Technology", "ADI": "Technology", "LRCX": "Technology", "MU": "Technology",
    "KLAC": "Technology", "CDNS": "Technology", "SNPS": "Technology", "MCHP": "Technology",
    "FTNT": "Technology", "CRWD": "Technology", "PANW": "Technology", "CTSH": "Technology",
    "IT": "Technology", "EPAM": "Technology", "HPQ": "Technology", "HPE": "Technology",
    "DELL": "Technology", "KEYS": "Technology", "CDW": "Technology", "GEN": "Technology",
    "FFIV": "Technology", "AKAM": "Technology", "ANET": "Technology", "GLW": "Technology",
    "APH": "Technology", "TEL": "Technology", "FSLR": "Technology", "ON": "Technology",
    "MPWR": "Technology", "SWKS": "Technology", "NXPI": "Technology", "ENPH": "Technology",
    "SEDG": "Technology", "FICO": "Technology",
    # Finance
    "BRK.B": "Finance", "JPM": "Finance", "V": "Finance", "MA": "Finance",
    "BAC": "Finance", "WFC": "Finance", "GS": "Finance", "MS": "Finance",
    "SPGI": "Finance", "BLK": "Finance", "AXP": "Finance", "C": "Finance",
    "SCHW": "Finance", "CB": "Finance", "MMC": "Finance", "PGR": "Finance",
    "AON": "Finance", "CME": "Finance", "ICE": "Finance", "MCO": "Finance",
    "MET": "Finance", "AIG": "Finance", "AFL": "Finance", "PRU": "Finance",
    "ALL": "Finance", "TRV": "Finance", "COF": "Finance", "BK": "Finance",
    "USB": "Finance", "PNC": "Finance", "FITB": "Finance", "CFG": "Finance",
    "HBAN": "Finance", "RF": "Finance", "KEY": "Finance", "MTB": "Finance",
    "STT": "Finance", "CINF": "Finance", "GL": "Finance", "RJF": "Finance",
    "BRO": "Finance", "WRB": "Finance", "AJG": "Finance", "FIS": "Finance",
    "FISV": "Finance", "FI": "Finance", "PYPL": "Finance", "SYF": "Finance",
    "DFS": "Finance", "CBOE": "Finance",
    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "LLY": "Healthcare", "ABBV": "Healthcare",
    "MRK": "Healthcare", "TMO": "Healthcare", "ABT": "Healthcare", "PFE": "Healthcare",
    "AMGN": "Healthcare", "MDT": "Healthcare", "ELV": "Healthcare", "CI": "Healthcare",
    "ISRG": "Healthcare", "GILD": "Healthcare", "VRTX": "Healthcare", "BSX": "Healthcare",
    "SYK": "Healthcare", "BDX": "Healthcare", "ZTS": "Healthcare", "REGN": "Healthcare",
    "HCA": "Healthcare", "DXCM": "Healthcare", "IQV": "Healthcare", "EW": "Healthcare",
    "IDXX": "Healthcare", "MTD": "Healthcare", "A": "Healthcare", "BAX": "Healthcare",
    "BIIB": "Healthcare", "HOLX": "Healthcare", "ALGN": "Healthcare", "RMD": "Healthcare",
    "COO": "Healthcare", "TECH": "Healthcare", "CRL": "Healthcare", "ILMN": "Healthcare",
    "MOH": "Healthcare", "CNC": "Healthcare", "HUM": "Healthcare", "CVS": "Healthcare",
    "CAH": "Healthcare", "MCK": "Healthcare", "GEHC": "Healthcare", "DVA": "Healthcare",
    "HSIC": "Healthcare", "INCY": "Healthcare", "VTRS": "Healthcare", "XRAY": "Healthcare",
    "DGX": "Healthcare", "LH": "Healthcare", "MRNA": "Healthcare", "BNTX": "Healthcare",
    "CRSP": "Healthcare", "EDIT": "Healthcare", "NTLA": "Healthcare", "BEAM": "Healthcare",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "NKE": "Consumer Discretionary", "LOW": "Consumer Discretionary",
    "BKNG": "Consumer Discretionary", "TJX": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "CMG": "Consumer Discretionary", "ORLY": "Consumer Discretionary", "AZO": "Consumer Discretionary",
    "ROST": "Consumer Discretionary", "MAR": "Consumer Discretionary", "HLT": "Consumer Discretionary",
    "DHI": "Consumer Discretionary", "LEN": "Consumer Discretionary", "PHM": "Consumer Discretionary",
    "NVR": "Consumer Discretionary", "GM": "Consumer Discretionary", "F": "Consumer Discretionary",
    "APTV": "Consumer Discretionary", "GRMN": "Consumer Discretionary", "EBAY": "Consumer Discretionary",
    "ETSY": "Consumer Discretionary", "POOL": "Consumer Discretionary", "BBY": "Consumer Discretionary",
    "DRI": "Consumer Discretionary", "YUM": "Consumer Discretionary",
    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PM": "Consumer Staples",
    "MO": "Consumer Staples", "MDLZ": "Consumer Staples", "CL": "Consumer Staples",
    "KMB": "Consumer Staples", "GIS": "Consumer Staples", "K": "Consumer Staples",
    "HSY": "Consumer Staples", "SJM": "Consumer Staples", "CAG": "Consumer Staples",
    "HRL": "Consumer Staples", "TSN": "Consumer Staples", "ADM": "Consumer Staples",
    "BG": "Consumer Staples", "STZ": "Consumer Staples", "TAP": "Consumer Staples",
    "SAM": "Consumer Staples", "KDP": "Consumer Staples", "MNST": "Consumer Staples",
    "EL": "Consumer Staples", "CHD": "Consumer Staples", "CLX": "Consumer Staples",
    "WBA": "Consumer Staples", "KR": "Consumer Staples", "SYY": "Consumer Staples",
    "DG": "Consumer Staples", "DLTR": "Consumer Staples", "TGT": "Consumer Staples",
    "BF.B": "Consumer Staples", "CPB": "Consumer Staples",
    # Communication Services
    "GOOGL": "Communication Services", "GOOG": "Communication Services", "META": "Communication Services",
    "NFLX": "Communication Services", "DIS": "Communication Services", "CMCSA": "Communication Services",
    "T": "Communication Services", "VZ": "Communication Services", "TMUS": "Communication Services",
    "CHTR": "Communication Services", "EA": "Communication Services", "TTWO": "Communication Services",
    "MTCH": "Communication Services", "WBD": "Communication Services", "PARA": "Communication Services",
    "OMC": "Communication Services", "IPG": "Communication Services", "LYV": "Communication Services",
    # Industrials
    "GE": "Industrials", "CAT": "Industrials", "UNP": "Industrials", "HON": "Industrials",
    "RTX": "Industrials", "BA": "Industrials", "DE": "Industrials", "LMT": "Industrials",
    "UPS": "Industrials", "ADP": "Industrials", "GD": "Industrials", "NOC": "Industrials",
    "MMM": "Industrials", "CSX": "Industrials", "NSC": "Industrials", "WM": "Industrials",
    "RSG": "Industrials", "EMR": "Industrials", "ETN": "Industrials", "ITW": "Industrials",
    "PH": "Industrials", "ROK": "Industrials", "CMI": "Industrials", "DOV": "Industrials",
    "FTV": "Industrials", "AME": "Industrials", "SWK": "Industrials", "IR": "Industrials",
    "GWW": "Industrials", "FAST": "Industrials", "CTAS": "Industrials", "PAYX": "Industrials",
    "VRSK": "Industrials", "CPRT": "Industrials", "ODFL": "Industrials", "DAL": "Industrials",
    "UAL": "Industrials", "LUV": "Industrials", "AAL": "Industrials", "FDX": "Industrials",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy",
    "EOG": "Energy", "PXD": "Energy", "MPC": "Energy", "VLO": "Energy",
    "PSX": "Energy", "OXY": "Energy", "WMB": "Energy", "KMI": "Energy",
    "OKE": "Energy", "FANG": "Energy", "DVN": "Energy", "HAL": "Energy",
    "BKR": "Energy", "HES": "Energy", "APA": "Energy", "EQT": "Energy",
    "CTRA": "Energy", "MRO": "Energy", "TRGP": "Energy",
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities", "D": "Utilities",
    "AEP": "Utilities", "SRE": "Utilities", "EXC": "Utilities", "XEL": "Utilities",
    "ED": "Utilities", "WEC": "Utilities", "ES": "Utilities", "AWK": "Utilities",
    "DTE": "Utilities", "ETR": "Utilities", "EIX": "Utilities", "FE": "Utilities",
    "PPL": "Utilities", "CMS": "Utilities", "ATO": "Utilities", "LNT": "Utilities",
    "EVRG": "Utilities", "AES": "Utilities", "CEG": "Utilities", "CNP": "Utilities",
    "NI": "Utilities", "PNW": "Utilities",
    # Real Estate
    "PLD": "Real Estate", "AMT": "Real Estate", "EQIX": "Real Estate", "CCI": "Real Estate",
    "PSA": "Real Estate", "DLR": "Real Estate", "O": "Real Estate", "SPG": "Real Estate",
    "WELL": "Real Estate", "VICI": "Real Estate", "AVB": "Real Estate", "EQR": "Real Estate",
    "ARE": "Real Estate", "MAA": "Real Estate", "UDR": "Real Estate", "ESS": "Real Estate",
    "EXR": "Real Estate", "CPT": "Real Estate", "REG": "Real Estate", "FRT": "Real Estate",
    "KIM": "Real Estate", "BXP": "Real Estate", "VTR": "Real Estate", "HST": "Real Estate",
    "PEAK": "Real Estate", "IRM": "Real Estate", "CBRE": "Real Estate", "CSGP": "Real Estate",
    # Materials
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials", "ECL": "Materials",
    "FCX": "Materials", "NUE": "Materials", "NEM": "Materials", "DOW": "Materials",
    "DD": "Materials", "PPG": "Materials", "VMC": "Materials", "MLM": "Materials",
    "ALB": "Materials", "EMN": "Materials", "CE": "Materials", "CF": "Materials",
    "MOS": "Materials", "IFF": "Materials", "FMC": "Materials", "BALL": "Materials",
    "AVY": "Materials", "PKG": "Materials", "IP": "Materials", "WRK": "Materials",
    "SEE": "Materials", "AMCR": "Materials", "CTVA": "Materials",
}

# Default sector for tickers not in the map
DEFAULT_SECTOR = "Technology"


def get_sector(ticker: str) -> str:
    """Get sector for a ticker, defaulting to Technology for unmapped ones."""
    return SECTOR_MAP.get(ticker, DEFAULT_SECTOR)


def seed_watchlist(csv_path: str | None = None, dsn: str | None = None) -> int:
    """Load seed watchlist from CSV into the stocks table.

    Args:
        csv_path: Path to the watchlist CSV file.
        dsn: Database connection string. Uses env vars if not provided.

    Returns:
        Number of stocks seeded.
    """
    if csv_path is None:
        csv_path = os.path.join(PROJECT_ROOT, "data", "watchlist_seed.csv")

    if dsn is None:
        db_host = os.environ.get("DB_HOST", "localhost")
        db_port = os.environ.get("DB_PORT", "5432")
        db_name = os.environ.get("DB_NAME", "stocks")
        db_user = os.environ.get("DB_USER", "postgres")
        db_password = os.environ.get("DB_PASSWORD", "")
        dsn = f"host={db_host} port={db_port} dbname={db_name} user={db_user} password={db_password}"

    # Read CSV
    stocks = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["ticker"].strip().upper()
            company_size = row["company_size"].strip().lower()
            sector = get_sector(ticker)
            # Use ticker as placeholder company name
            company_name = ticker
            stocks.append((ticker, company_name, sector, company_size))

    if not stocks:
        print("No stocks found in CSV file.")
        return 0

    # Insert into database
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO stocks (ticker, company_name, sector, company_size)
                VALUES %s
                ON CONFLICT (ticker) DO NOTHING
                """,
                stocks,
            )
        conn.commit()
        print(f"Seeded {len(stocks)} stocks into the watchlist.")
        return len(stocks)
    finally:
        conn.close()


if __name__ == "__main__":
    seed_watchlist()
