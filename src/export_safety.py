"""CSV values are data, not spreadsheet formulas."""
import pandas as pd


def safe_csv(frame: pd.DataFrame) -> bytes:
    def cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    return frame.map(cell).to_csv(index=False).encode("utf-8-sig")
