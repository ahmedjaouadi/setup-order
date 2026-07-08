#!/usr/bin/env python3
"""Populate missing sectors for stocks in symbol_metadata table."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trading_state.sqlite"

# Stock sectors mapping (fallback/supplement)
SECTOR_MAPPING = {
    "AAOI": "Technology",
    "ACHR": "Healthcare",
    "AEHR": "Technology",
    "AHMA": "Materials",
    "ALGM": "Technology",
    "AMD": "Technology",
    "AMPX": "Technology",
    "ARM": "Technology",
    "AVGO": "Technology",
    "BGC": "Financials",
    "CAST": "Technology",
    "CODI": "Real Estate",
    "CRDO": "Energy",
    "DXYZ": "Industrials",
    "FLNC": "Industrials",
    "GEV": "Industrials",
    "GILT": "Technology",
    "HIMX": "Technology",
    "HON": "Industrials",
    "HOOD": "Financials",
    "IBM": "Technology",
    "INOD": "Technology",
    "INTC": "Technology",
    "IONQ": "Technology",
    "IRDM": "Healthcare",
    "IREN": "Utilities",
    "JOBY": "Industrials",
    "LITE": "Technology",
    "LPTH": "Industrials",
    "LUNR": "Industrials",
    "MRVL": "Technology",
    "NBIS": "Industrials",
    "NET": "Technology",
    "NOK": "Technology",
    "NOW": "Technology",
    "NVTS": "Healthcare",
    "ONDS": "Technology",
    "PLAB": "Technology",
    "POWI": "Industrials",
    "QBTS": "Technology",
    "QCOM": "Technology",
    "RDW": "Materials",
    "RGNT": "Financials",
    "RKLB": "Industrials",
    "SHOP": "Consumer Discretionary",
    "SMCI": "Technology",
    "SOFI": "Financials",
    "STM": "Technology",
    "TXN": "Technology",
    "UEC": "Materials",
    "VST": "Materials",
}


def populate_sectors():
    """Populate missing sectors in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all unique symbols
    cursor.execute("""
        SELECT DISTINCT symbol FROM (
            SELECT symbol FROM setups
            UNION
            SELECT symbol FROM opportunities
            UNION
            SELECT symbol FROM forecast_metrics
        ) ORDER BY symbol
    """)
    all_symbols = [row[0] for row in cursor.fetchall()]

    print(f"Found {len(all_symbols)} unique symbols to process\n")

    for symbol in all_symbols:
        sector = SECTOR_MAPPING.get(symbol)
        company_name = symbol

        if not sector:
            print(f"{symbol}: ⚠ Not found in mapping")
            continue

        print(f"{symbol}: {sector}")

        # Insert or update symbol_metadata
        cursor.execute(
            """
            INSERT INTO symbol_metadata (symbol, company_name, sector, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
                sector = excluded.sector,
                company_name = COALESCE(excluded.company_name, company_name),
                updated_at = CURRENT_TIMESTAMP
            """,
            (symbol, company_name, sector),
        )

    conn.commit()

    # Print summary
    cursor.execute(
        """
        SELECT sector, COUNT(*) as count
        FROM symbol_metadata
        WHERE sector IS NOT NULL AND sector != ''
        GROUP BY sector
        ORDER BY count DESC
    """
    )
    print("\n===== SUMMARY BY SECTOR =====")
    for sector, count in cursor.fetchall():
        print(f"{sector}: {count} stocks")

    conn.close()


if __name__ == "__main__":
    populate_sectors()
